"""End-to-end meeting processing pipeline.

Stages: validate → download → media → transcribe → speakers/persist transcript
→ intelligence → persist results → complete. Each stage records a
processing_job_events row; failures are classified retryable/fatal and either
postponed (bounded backoff) or recorded terminally.
"""

import logging
import os
import random
import tempfile
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
from app.queue.adapter import JOB_TYPE_CAPTURE_SYNC, QueueMessage
from app.store.supabase_store import SupabaseStore
from app.transcript.model import NormalizedSegment, TranscriptChunk
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


def _flatten_live_intel(intel) -> list[dict]:
    """Flatten MeetingIntelligence into live_intelligence_items rows. The
    provisional set keeps evidence as live-segment indices; timestamps and
    speaker labels are carried so the UI can anchor each candidate."""
    items: list[dict] = []

    def add(kind: str, content: str, speaker: str | None,
            ts: int | None, evidence: list[int]) -> None:
        items.append(
            {
                "item_index": len(items),
                "kind": kind,
                "content": content,
                "speaker_label": speaker,
                "timestamp_ms": ts,
                "evidence_segment_indices": evidence,
            }
        )

    for kp in intel.key_points:
        add("key_point", kp.text, None, kp.timestamp_ms,
            kp.evidence_segment_indices)
    for d in intel.decisions:
        add("decision", d.text, d.speaker_label, d.timestamp_ms,
            d.evidence_segment_indices)
    for a in intel.action_items:
        add("action_item", a.task, a.owner_speaker_label, a.timestamp_ms,
            a.evidence_segment_indices)
    for q in intel.open_questions:
        add("question", q.question, None, q.timestamp_ms,
            q.evidence_segment_indices)
    return items


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
        if msg.job_type == JOB_TYPE_CAPTURE_SYNC:
            return self.sync_capture(msg)
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
                # Every capture source converges on a stored media file; the
                # source_type enum is informational here.
                if not meeting.get("source_file_path"):
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

                known_speakers, known_profiles = self._known_speakers(owner_id)

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
                speaker_map = self.store.map_speakers(
                    meeting_id=meeting_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    speaker_labels=labels,
                    known_profiles=known_profiles,
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

    # ------------------------------------------------------------- live sync

    def sync_capture(self, msg: QueueMessage) -> tuple[str, int]:
        """Transcribe the not-yet-synced audio tail of a capture session.

        Parts are MediaRecorder timeslice fragments of one continuous
        container; byte-concatenating them reconstructs a decodable file. One
        transcribe call per contiguous run. Committed atomically via
        commit_capture_sync — a false return means a concurrent sync won.
        """
        session = self.store.get_capture_session(msg.capture_session_id)
        if session is None:
            raise InvalidMessageError("Capture session no longer exists.")

        parts = self.store.list_capture_parts(session["id"])
        # NB: `or -1` would be wrong — cursor 0 is falsy but valid.
        raw_cursor = session.get("transcribed_part_index")
        cursor = -1 if raw_cursor is None else int(raw_cursor)
        start_ms = int(session.get("transcribed_ms_end") or 0)

        # Longest contiguous 'uploaded' run starting at cursor+1.
        run: list[dict] = []
        expected = cursor + 1
        for part in parts:
            idx = int(part["part_index"])
            if idx <= cursor:
                continue
            if idx != expected or part["status"] != "uploaded":
                break
            run.append(part)
            expected += 1
        if not run:
            return "done", 0  # nothing new — message already converged

        new_cursor = int(run[-1]["part_index"])
        try:
            concat_path = self._concat_parts(session["id"], parts, new_cursor)

            with ffmpeg.make_work_dir(self.settings.temp_dir) as work:
                # Slice to EOF: the live concat is streaming WebM with no
                # duration metadata, so measure the output MP3 instead.
                slice_path = ffmpeg.extract_audio_slice(
                    concat_path, start_ms / 1000, None,
                    f"{work}/live_slice.mp3",
                )
                slice_info = ffmpeg.probe(slice_path)
                end_ms = start_ms + int(round(slice_info.duration_seconds * 1000))
                if end_ms <= start_ms:
                    # New part not yet decodable past the watermark.
                    return "retry", 15
                known_refs, _ = self._known_speakers(session["owner_id"])
                result = self.transcription.transcribe(
                    TranscriptChunk(
                        path=slice_path, offset_seconds=start_ms / 1000
                    ),
                    known_refs,
                )
            segments = merge_chunk_segments([result.segments])
            committed = self.store.commit_capture_sync(
                session_id=session["id"],
                expected_cursor=cursor,
                new_cursor=new_cursor,
                new_ms_end=end_ms,
                segments=[
                    {
                        "segment_index": s.segment_index,
                        "speaker_label": s.speaker_label,
                        "start_ms": s.start_ms,
                        "end_ms": s.end_ms,
                        "text": s.text,
                    }
                    for s in segments
                ],
            )
            if committed:
                self._maybe_sync_intel(session["id"], new_cursor)
            return ("done" if committed else "retry"), (0 if committed else 5)
        except PipelineError as exc:
            # Transient failures leave parts 'uploaded'; the next part's
            # message converges the session anyway.
            log.warning(
                "capture sync failed", extra={"session_id": session["id"]},
                exc_info=exc,
            )
            return "retry", 30

    def _concat_parts(
        self, session_id: str, parts: list[dict], through_index: int
    ) -> str:
        """Persisted concat cache: {temp}/banner-live-{session} holds the bytes
        of parts 0..K; a sidecar .idx records K. Appends or rebuilds as needed."""
        base = self.settings.temp_dir or tempfile.gettempdir()
        cache = os.path.join(base, f"banner-live-{session_id}")
        sidecar = f"{cache}.idx"

        cached_through = -1
        if os.path.exists(cache) and os.path.exists(sidecar):
            try:
                with open(sidecar) as fh:
                    cached_through = int(fh.read().strip())
            except (ValueError, OSError):
                cached_through = -1

        # Cache can only append contiguously from where it left off.
        if cached_through >= through_index:
            return cache

        # Append only when the sidecar proves what the cache already holds.
        mode = "ab" if cached_through >= 0 else "wb"
        if mode == "wb":
            cached_through = -1
        with open(cache, mode) as fh:
            for part in parts:
                idx = int(part["part_index"])
                if idx <= cached_through or idx > through_index:
                    continue
                fh.write(self.store.download_source(part["storage_path"]))
                cached_through = idx
        with open(sidecar, "w") as fh:
            fh.write(str(cached_through))
        return cache

    def _maybe_sync_intel(self, session_id: str, new_cursor: int) -> None:
        """Regenerate live candidate intelligence when enough new audio has
        been transcribed. Failures never block transcript sync — the next
        commit retries the analysis."""
        try:
            fresh = self.store.get_capture_session(session_id)
            if not fresh:
                return
            raw = fresh.get("intel_part_index")
            intel_cursor = -1 if raw is None else int(raw)
            if new_cursor - intel_cursor < self.settings.live_intel_part_interval:
                return
            rows = self.store.list_live_segments(session_id)
            if not rows:
                return
            segments = [
                NormalizedSegment(
                    segment_index=i,
                    start_ms=int(r["start_ms"]),
                    end_ms=int(r["end_ms"]),
                    speaker_label=r.get("speaker_label"),
                    text=r["text"],
                )
                for i, r in enumerate(rows)
            ]
            result = self.intelligence.analyze(
                render_transcript_for_prompt(segments),
                prompt_version="live_intel_v1",
            )
            intel = validate_evidence(result.data, len(segments))
            items = _flatten_live_intel(intel)
            self.store.commit_capture_intel(
                session_id=session_id,
                expected_cursor=intel_cursor,
                new_cursor=new_cursor,
                items=items,
            )
        except Exception as exc:
            log.warning(
                "live intel run failed", extra={"session_id": session_id},
                exc_info=exc,
            )

    # ----------------------------------------------------------------- helpers

    def _known_speakers(
        self, owner_id: str
    ) -> tuple[list[KnownSpeakerRef], dict[str, str]]:
        """Load enrolled speaker profiles. Returns (provider refs, name ->
        profile_id). Only clips the owner explicitly enrolled are used as
        references, but every profile name is a valid known-speaker label."""
        refs: list[KnownSpeakerRef] = []
        profiles: dict[str, str] = {}
        for row in self.store.speaker_profiles_for_owner(owner_id):
            profiles[row["display_name"]] = row["id"]
            # The provider accepts at most 4 reference clips.
            if not row.get("reference_storage_path") or len(refs) >= 4:
                continue
            try:
                data = self.store.download_source(row["reference_storage_path"])
            except Exception as exc:
                log.warning("could not load speaker reference", exc_info=exc)
                continue
            refs.append(
                KnownSpeakerRef(name=row["display_name"], audio_bytes=data)
            )
        return refs, profiles

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
