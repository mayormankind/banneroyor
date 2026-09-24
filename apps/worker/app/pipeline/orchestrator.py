"""End-to-end meeting processing pipeline.

Stages: validate → download → media → transcribe → speakers/persist transcript
→ intelligence → persist results → complete. Each stage records a
processing_job_events row; failures are classified retryable/fatal and either
postponed (bounded backoff) or recorded terminally.
"""

import logging
import random
import time
from contextlib import contextmanager
from typing import Any

from app.config.settings import Settings
from app.intelligence.schema import merge_chunk_results, validate_evidence
from app.media import ffmpeg
from app.pipeline.errors import (
    InvalidMessageError,
    MediaValidationError,
    MissingSourceError,
    PipelineError,
    RetryableError,
)
from app.providers.base import (
    IntelligenceProvider,
    KnownSpeakerRef,
    TranscriptionProvider,
)
from app.queue.adapter import QueueMessage
from app.store.supabase_store import SupabaseStore
from app.transcript.model import TranscriptChunk
from app.transcript.normalize import (
    chunk_segments_by_chars,
    merge_chunk_segments,
    render_transcript_for_prompt,
)

log = logging.getLogger(__name__)

STAGES = [
    "validate",
    "download",
    "media",
    "transcribe",
    "persist_transcript",
    "intelligence",
    "persist_results",
]

MAX_BACKOFF_SECONDS = 900
BASE_BACKOFF_SECONDS = 30


def backoff_seconds(attempt: int) -> int:
    """Exponential backoff with jitter, bounded."""
    base = BASE_BACKOFF_SECONDS * (2 ** max(0, attempt - 1))
    return int(min(MAX_BACKOFF_SECONDS, base + random.uniform(0, base * 0.25)))


class Pipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        store: SupabaseStore,
        transcription: TranscriptionProvider,
        intelligence: IntelligenceProvider,
    ):
        self.settings = settings
        self.store = store
        self.transcription = transcription
        self.intelligence = intelligence

    # ------------------------------------------------------------------ stages

    @contextmanager
    def stage(self, job: dict, name: str):
        """Record a stage event and update job.current_stage."""
        job_id, meeting_id = job["id"], job["meeting_id"]
        attempt = int(job.get("attempts") or 1)
        self.store.set_stage(job_id, name)
        self.store.record_event(
            job_id=job_id, meeting_id=meeting_id, stage=name,
            status="started", attempt=attempt,
        )
        started = time.monotonic()
        try:
            yield
        except PipelineError as exc:
            self.store.record_event(
                job_id=job_id, meeting_id=meeting_id, stage=name,
                status="failed", attempt=attempt,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code=exc.error_code, error_message=exc.safe_message,
            )
            raise
        except Exception as exc:
            self.store.record_event(
                job_id=job_id, meeting_id=meeting_id, stage=name,
                status="failed", attempt=attempt,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="unexpected", error_message=str(exc)[:500],
            )
            raise
        else:
            self.store.record_event(
                job_id=job_id, meeting_id=meeting_id, stage=name,
                status="completed", attempt=attempt,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    # ------------------------------------------------------------------- main

    def process(self, msg: QueueMessage) -> tuple[str, int]:
        """Process one queue message. Returns (action, delay_seconds) where
        action is 'done' | 'retry' | 'failed' and delay_seconds is the backoff
        the caller should apply before the message becomes visible again."""
        job = self.store.get_job(msg.job_id)
        meeting = self.store.get_meeting(msg.meeting_id)

        if job is None or meeting is None:
            raise InvalidMessageError(
                "The processing job or meeting no longer exists."
            )
        if job["meeting_id"] != meeting["id"]:
            raise InvalidMessageError(
                "Job/meeting mismatch.", detail=f"{job['id']} vs {meeting['id']}"
            )
        if job["status"] in ("succeeded", "failed", "dead"):
            # Duplicate delivery of an already-finished job.
            log.info("job already terminal, acking", extra={"job_id": job["id"]})
            return "done", 0
        if job["status"] == "running" and job.get("attempts", 0) > 0:
            # Another worker delivery is in-flight or crashed mid-run. Re-claim
            # is still safe because persistence is run-scoped/idempotent.
            pass

        if not self.store.claim_job(msg.job_id):
            log.info("job claim lost (duplicate delivery)", extra={"job_id": job["id"]})
            return "done", 0
        job = self.store.get_job(msg.job_id) or job
        meeting_status = meeting["status"]
        if meeting_status == "queued":
            self.store.client.table("meetings").update(
                {"status": "processing"}
            ).eq("id", meeting["id"]).execute()

        run_id = job["run_id"]
        meeting_id = meeting["id"]
        owner_id = meeting["owner_id"]
        cost: dict[str, Any] = {"worker_version": self.settings.worker_version}

        try:
            with self.stage(job, "validate"):
                if meeting["source_type"] != "upload" or not meeting.get(
                    "source_file_path"
                ):
                    raise MissingSourceError()

            with self.stage(job, "download"):
                audio_bytes = self.store.download_source(meeting["source_file_path"])

            with ffmpeg.make_work_dir(self.settings.temp_dir) as work_dir:
                src = f"{work_dir}/source_media"
                with open(src, "wb") as fh:
                    fh.write(audio_bytes)

                with self.stage(job, "media"):
                    chunks, info = ffmpeg.prepare_audio_chunks(
                        src,
                        work_dir,
                        chunk_seconds=self.settings.transcribe_chunk_seconds,
                        max_file_bytes=self.settings.transcribe_max_file_bytes,
                    )
                    duration_seconds = int(round(info.duration_seconds))

                known_speakers = self._known_speaker_refs(owner_id)

                with self.stage(job, "transcribe"):
                    per_chunk: list[list] = []
                    usage_all: list[dict] = []
                    for ch in chunks:
                        result = self.transcription.transcribe(
                            TranscriptChunk(path=ch.path, offset_seconds=ch.offset_seconds),
                            known_speakers,
                        )
                        per_chunk.append(result.segments)
                        usage_all.append(result.usage)
                    segments = merge_chunk_segments(per_chunk)
                    cost["transcription"] = {
                        "model": self.transcription.model,
                        "chunks": len(chunks),
                        "usage": usage_all,
                    }

            with self.stage(job, "persist_transcript"):
                index_to_id = self.store.save_transcript(
                    meeting_id=meeting_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    segments=segments,
                )
                labels = sorted(
                    {s.speaker_label for s in segments if s.speaker_label}
                )
                known_names = {r.name for r in known_speakers}
                speaker_map = self.store.map_speakers(
                    meeting_id=meeting_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    speaker_labels=labels,
                    known_names=known_names,
                )

            with self.stage(job, "intelligence"):
                if not segments:
                    raise MediaValidationError(
                        "The recording produced no transcript to analyze."
                    )
                text_chunks = chunk_segments_by_chars(
                    segments, self.settings.intelligence_chunk_chars
                )
                analyses = []
                intel_usage: list[dict] = []
                for tc in text_chunks:
                    res = self.intelligence.analyze(
                        render_transcript_for_prompt(tc),
                        prompt_version="intelligence_v1",
                    )
                    analyses.append(res.data)
                    intel_usage.append(res.usage)
                merged = merge_chunk_results(analyses)
                merged = validate_evidence(merged, len(segments))
                cost["intelligence"] = {
                    "model": self.intelligence.model,
                    "prompt_version": "intelligence_v1",
                    "chunks": len(text_chunks),
                    "usage": intel_usage,
                }

            with self.stage(job, "persist_results"):
                self.store.save_intelligence(
                    meeting_id=meeting_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    intel=merged,
                    index_to_id=index_to_id,
                    generation_version="intelligence_v1",
                    model=self.intelligence.model,
                    speaker_to_participant=speaker_map,
                )

            self.store.complete_job(
                job_id=job["id"],
                meeting_id=meeting_id,
                run_id=run_id,
                duration_seconds=duration_seconds,
                cost_metadata=cost,
            )
            return "done", 0

        except PipelineError as exc:
            return self._handle_failure(job, meeting, exc)
        except Exception as exc:
            log.exception("unexpected pipeline error", extra={"job_id": job["id"]})
            return self._handle_failure(
                job,
                meeting,
                RetryableError(
                    "Processing hit an unexpected error; it will be retried.",
                    detail=str(exc)[:500],
                ),
            )

    # ----------------------------------------------------------------- helpers

    def _known_speaker_refs(self, owner_id: str) -> list[KnownSpeakerRef]:
        """Load known-speaker reference clips for provider-side identity hints.
        Only clips the owner explicitly enrolled are used."""
        refs: list[KnownSpeakerRef] = []
        for row in self.store.speaker_reference_clips(owner_id):
            try:
                data = self.store.download_source(row["reference_storage_path"])
            except Exception as exc:
                log.warning("could not load speaker reference", exc_info=exc)
                continue
            refs.append(
                KnownSpeakerRef(name=row["display_name"], audio_bytes=data)
            )
        return refs

    def _handle_failure(
        self, job: dict, meeting: dict, exc: PipelineError
    ) -> tuple[str, int]:
        attempts = int(job.get("attempts") or 1)
        retryable = isinstance(exc, RetryableError) and attempts < int(
            job.get("max_attempts") or self.settings.max_job_attempts
        )
        delay = backoff_seconds(attempts) if retryable else 0
        diagnostic = {"detail": exc.detail} if exc.detail else None
        next_attempt = None
        if retryable:
            from datetime import UTC, datetime, timedelta

            next_attempt = (
                datetime.now(UTC) + timedelta(seconds=delay)
            ).isoformat()
        self.store.fail_job(
            job_id=job["id"],
            meeting_id=meeting["id"],
            error_code=exc.error_code,
            error_message=exc.safe_message,
            diagnostic=diagnostic,
            retryable=retryable,
            next_attempt_at=next_attempt,
        )
        return ("retry" if retryable else "failed"), delay
