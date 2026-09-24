"""Persistence layer over Supabase. All writes are run-scoped so a reprocessing
run never disturbs the currently displayed report; meetings.current_run_id only
advances after the full run validates and persists."""

import logging
from datetime import UTC, date, datetime
from typing import Any

from supabase import Client

from app.intelligence.schema import MeetingIntelligence
from app.transcript.model import NormalizedSegment

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _iso_date_or_none(value: str | None) -> str | None:
    """Model output may contain fuzzy deadlines ('next Friday') — persist only
    real ISO dates, otherwise null."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None

INSERT_BATCH = 500


def friendly_speaker_name(label: str) -> str:
    """'A' -> 'Speaker 1'; non-letter labels are returned as-is."""
    if len(label) == 1 and "A" <= label <= "Z":
        return f"Speaker {ord(label) - 64}"
    return label


class SupabaseStore:
    def __init__(self, client: Client, bucket: str):
        self.client = client
        self.bucket = bucket

    # --- reads --------------------------------------------------------------

    def get_meeting(self, meeting_id: str) -> dict | None:
        res = (
            self.client.table("meetings")
            .select("*")
            .eq("id", meeting_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def get_job(self, job_id: str) -> dict | None:
        res = (
            self.client.table("processing_jobs")
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def claim_job(self, job_id: str) -> bool:
        """Atomically move a queued job to running. Returns False if another
        delivery already claimed it (idempotent duplicate handling)."""
        res = (
            self.client.table("processing_jobs")
            .select("attempts")
            .eq("id", job_id)
            .eq("status", "queued")
            .limit(1)
            .execute()
        )
        if not res.data:
            return False
        res2 = (
            self.client.table("processing_jobs")
            .update(
                {
                    "status": "running",
                    "attempts": res.data[0]["attempts"] + 1,
                    "started_at": _utcnow(),
                    "error_code": None,
                    "error_message": None,
                }
            )
            .eq("id", job_id)
            .eq("status", "queued")
            .execute()
        )
        return bool(res2.data)

    def download_source(self, path: str) -> bytes:
        return self.client.storage.from_(self.bucket).download(path)

    def speaker_reference_clips(self, owner_id: str) -> list[dict]:
        res = (
            self.client.table("speaker_profiles")
            .select("display_name, reference_storage_path")
            .eq("owner_id", owner_id)
            .not_.is_("reference_storage_path", "null")
            .limit(4)
            .execute()
        )
        return res.data or []

    # --- job bookkeeping -----------------------------------------------------

    def record_event(
        self,
        *,
        job_id: str,
        meeting_id: str,
        stage: str,
        status: str,
        attempt: int,
        duration_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "job_id": job_id,
            "meeting_id": meeting_id,
            "stage": stage,
            "status": status,
            "attempt_number": attempt,
            "metadata": metadata or {},
        }
        if status == "started":
            row["started_at"] = _utcnow()
        else:
            row["completed_at"] = _utcnow()
            if duration_ms is not None:
                row["duration_ms"] = duration_ms
        if error_code:
            row["error_code"] = error_code
        if error_message:
            row["error_message"] = error_message[:1000]
        self.client.table("processing_job_events").insert(row).execute()

    def set_stage(self, job_id: str, stage: str, progress: float | None = None) -> None:
        payload: dict[str, Any] = {"current_stage": stage}
        if progress is not None:
            payload["progress"] = progress
        self.client.table("processing_jobs").update(payload).eq("id", job_id).execute()

    def complete_job(
        self,
        *,
        job_id: str,
        meeting_id: str,
        run_id: str,
        duration_seconds: int | None,
        cost_metadata: dict,
    ) -> None:
        self.client.table("processing_jobs").update(
            {
                "status": "succeeded",
                "completed_at": _utcnow(),
                "current_stage": "done",
                "progress": 1,
                "cost_metadata": cost_metadata,
            }
        ).eq("id", job_id).execute()
        self.client.table("meetings").update(
            {
                "status": "completed",
                "current_run_id": run_id,
                "duration_seconds": duration_seconds,
                "failure_reason": None,
            }
        ).eq("id", meeting_id).execute()

    def fail_job(
        self,
        *,
        job_id: str,
        meeting_id: str,
        error_code: str,
        error_message: str,
        diagnostic: dict | None = None,
        retryable: bool,
        next_attempt_at: str | None = None,
    ) -> None:
        status = "queued" if retryable else "failed"
        self.client.table("processing_jobs").update(
            {
                "status": status,
                "error_code": error_code,
                "error_message": error_message[:1000],
                "diagnostic": diagnostic or {},
                "next_attempt_at": next_attempt_at,
                "completed_at": None if retryable else _utcnow(),
            }
        ).eq("id", job_id).execute()
        if retryable:
            self.client.table("meetings").update(
                {"status": "queued"}
            ).eq("id", meeting_id).execute()
        else:
            self.client.table("meetings").update(
                {"status": "failed", "failure_reason": error_message[:1000]}
            ).eq("id", meeting_id).execute()

    # --- result persistence ---------------------------------------------------

    def save_transcript(
        self,
        *,
        meeting_id: str,
        owner_id: str,
        run_id: str,
        segments: list[NormalizedSegment],
    ) -> dict[int, str]:
        """Idempotent run-scoped insert. Returns segment_index -> row id."""
        self.client.table("transcript_segments").delete().eq(
            "meeting_id", meeting_id
        ).eq("run_id", run_id).execute()

        index_to_id: dict[int, str] = {}
        for i in range(0, len(segments), INSERT_BATCH):
            batch = segments[i : i + INSERT_BATCH]
            rows = [s.as_row(meeting_id, owner_id, run_id) for s in batch]
            res = self.client.table("transcript_segments").insert(rows).execute()
            for seg, row in zip(batch, res.data or [], strict=True):
                index_to_id[seg.segment_index] = row["id"]
        return index_to_id

    def map_speakers(
        self,
        *,
        meeting_id: str,
        owner_id: str,
        run_id: str,
        speaker_labels: list[str],
        known_names: set[str],
    ) -> dict[str, str]:
        """Ensure each raw speaker label has a participant row and link
        segments. Returns speaker_label -> participant_id."""
        mapping: dict[str, str] = {}
        for label in speaker_labels:
            res = (
                self.client.table("participants")
                .select("id")
                .eq("meeting_id", meeting_id)
                .eq("speaker_label", label)
                .limit(1)
                .execute()
            )
            if res.data:
                pid = res.data[0]["id"]
            else:
                # If the label is a verified known-speaker name, keep it;
                # otherwise use a neutral display name.
                display = label if label in known_names else friendly_speaker_name(label)
                ins = (
                    self.client.table("participants")
                    .insert(
                        {
                            "meeting_id": meeting_id,
                            "owner_id": owner_id,
                            "display_name": display,
                            "speaker_label": label,
                        }
                    )
                    .execute()
                )
                pid = ins.data[0]["id"]
            mapping[label] = pid

            self.client.table("transcript_segments").update(
                {"participant_id": pid}
            ).eq("meeting_id", meeting_id).eq("run_id", run_id).eq(
                "speaker_label", label
            ).execute()
        return mapping

    def save_intelligence(
        self,
        *,
        meeting_id: str,
        owner_id: str,
        run_id: str,
        intel: MeetingIntelligence,
        index_to_id: dict[int, str],
        generation_version: str,
        model: str,
        speaker_to_participant: dict[str, str],
    ) -> None:
        """Persist validated intelligence rows for this run. Evidence segment
        indices are resolved to real segment ids; unknown indices are dropped."""

        def evidence(indices: list[int]) -> list[str]:
            return [index_to_id[i] for i in indices if i in index_to_id]

        def participant_for(label: str | None) -> str | None:
            return speaker_to_participant.get(label) if label else None

        # Clear any prior rows for this run (retry-safe).
        for table in ("summaries", "key_points", "decisions", "action_items", "questions"):
            self.client.table(table).delete().eq("meeting_id", meeting_id).eq(
                "run_id", run_id
            ).execute()

        self.client.table("summaries").insert(
            {
                "meeting_id": meeting_id,
                "owner_id": owner_id,
                "run_id": run_id,
                "summary": intel.summary,
                "generation_version": generation_version,
                "model": model,
            }
        ).execute()

        if intel.key_points:
            self.client.table("key_points").insert(
                [
                    {
                        "meeting_id": meeting_id,
                        "owner_id": owner_id,
                        "run_id": run_id,
                        "content": k.text,
                        "sort_order": i,
                        "evidence_segment_ids": evidence(k.evidence_segment_indices),
                        "timestamp_ms": k.timestamp_ms,
                        "generation_version": generation_version,
                    }
                    for i, k in enumerate(intel.key_points)
                ]
            ).execute()

        if intel.decisions:
            self.client.table("decisions").insert(
                [
                    {
                        "meeting_id": meeting_id,
                        "owner_id": owner_id,
                        "run_id": run_id,
                        "description": d.text,
                        "speaker_label": d.speaker_label,
                        "participant_id": participant_for(d.speaker_label),
                        "evidence_segment_ids": evidence(d.evidence_segment_indices),
                        "timestamp_ms": d.timestamp_ms,
                        "generation_version": generation_version,
                    }
                    for d in intel.decisions
                ]
            ).execute()

        if intel.action_items:
            self.client.table("action_items").insert(
                [
                    {
                        "meeting_id": meeting_id,
                        "owner_id": owner_id,
                        "run_id": run_id,
                        "description": a.task,
                        "owner_participant_id": participant_for(a.owner_speaker_label),
                        "owner_speaker_label": a.owner_speaker_label,
                        "due_date": _iso_date_or_none(a.deadline),
                        "status": "open",
                        "evidence_segment_ids": evidence(a.evidence_segment_indices),
                        "timestamp_ms": a.timestamp_ms,
                        "generation_version": generation_version,
                    }
                    for a in intel.action_items
                ]
            ).execute()

        if intel.open_questions:
            self.client.table("questions").insert(
                [
                    {
                        "meeting_id": meeting_id,
                        "owner_id": owner_id,
                        "run_id": run_id,
                        "question": q.question,
                        "resolved": q.resolved,
                        "evidence_segment_ids": evidence(q.evidence_segment_indices),
                        "timestamp_ms": q.timestamp_ms,
                        "generation_version": generation_version,
                    }
                    for q in intel.open_questions
                ]
            ).execute()
