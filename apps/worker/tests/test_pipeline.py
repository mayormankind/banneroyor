"""End-to-end pipeline test against an in-memory store, fake providers, and a
monkeypatched media stage — no network, no ffmpeg, no OpenAI."""

import pytest

from app.config.settings import Settings
from app.media import ffmpeg
from app.pipeline.orchestrator import Pipeline
from app.providers.fake import FakeIntelligenceProvider, FakeTranscriptionProvider
from app.queue.adapter import QueueMessage
from app.store.supabase_store import friendly_speaker_name


class FakeTable:
    """Minimal supabase-py table shim for tests."""

    def __init__(self, store: "MemoryStore", name: str):
        self._store = store
        self._name = name
        self._filters: list[tuple[str, object]] = []
        self._update: dict | None = None
        self._insert: list[dict] | None = None

    # query builder bits
    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def not_(self):
        return self

    def is_(self, col, val):
        return self

    def limit(self, n):
        self._filters.append(("__limit__", n))
        return self

    def update(self, payload):
        self._update = payload
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def delete(self):
        self._update = {"__delete__": True}
        return self

    def execute(self):
        table = self._store.tables.setdefault(self._name, [])
        if self._insert is not None:
            for row in self._insert:
                row = dict(row)
                row.setdefault("id", f"{self._name}-{len(table)+1}")
                table.append(row)
            return type("R", (), {"data": self._insert})()
        if self._update is not None:
            if self._update.get("__delete__"):
                kept = [
                    r for r in table
                    if not all(r.get(c) == v for c, v in self._filters)
                ]
                self._store.tables[self._name] = kept
                return type("R", (), {"data": []})()
            updated = [
                r for r in table
                if all(r.get(c) == v for c, v in self._filters)
            ]
            for r in updated:
                r.update(self._update)
            return type("R", (), {"data": updated})()
        rows = [
            r for r in table
            if all(r.get(c) == v for c, v in self._filters if c != "__limit__")
        ]
        limit = next((v for c, v in self._filters if c == "__limit__"), None)
        if limit:
            rows = rows[:limit]
        return type("R", (), {"data": rows})()


class MemoryStore:
    """SupabaseStore-shaped fake with in-memory tables."""

    def __init__(self, meeting: dict, job: dict):
        self.tables = {
            "meetings": [meeting],
            "processing_jobs": [job],
            "processing_job_events": [],
            "transcript_segments": [],
            "participants": [],
            "summaries": [],
            "key_points": [],
            "decisions": [],
            "action_items": [],
            "questions": [],
        }
        self.client = type("C", (), {"table": lambda s, n: FakeTable(self, n)})()

    def get_meeting(self, mid):
        return next((m for m in self.tables["meetings"] if m["id"] == mid), None)

    def get_job(self, jid):
        return next((j for j in self.tables["processing_jobs"] if j["id"] == jid), None)

    def claim_job(self, jid):
        job = self.get_job(jid)
        if job["status"] != "queued":
            return False
        job["status"] = "running"
        job["attempts"] += 1
        return True

    def download_source(self, path):
        return b"fake-audio-bytes"

    def speaker_reference_clips(self, owner_id):
        return []

    def record_event(self, **kw):
        self.tables["processing_job_events"].append(kw)

    def set_stage(self, job_id, stage, progress=None):
        self.get_job(job_id)["current_stage"] = stage

    def complete_job(self, *, job_id, meeting_id, run_id, duration_seconds, cost_metadata):
        self.get_job(job_id).update(status="succeeded", cost_metadata=cost_metadata)
        self.get_meeting(meeting_id).update(
            status="completed", current_run_id=run_id, duration_seconds=duration_seconds
        )

    def fail_job(
        self, *, job_id, meeting_id, error_code, error_message,
        diagnostic, retryable, next_attempt_at,
    ):
        self.get_job(job_id).update(
            status="queued" if retryable else "failed",
            error_code=error_code,
            error_message=error_message,
        )
        self.get_meeting(meeting_id).update(
            status="queued" if retryable else "failed",
            failure_reason=error_message,
        )

    # run-scoped persistence (mirrors SupabaseStore semantics)
    def save_transcript(self, *, meeting_id, owner_id, run_id, segments):
        self.tables["transcript_segments"] = [
            s for s in self.tables["transcript_segments"] if s["run_id"] != run_id
        ]
        idx = {}
        for s in segments:
            row = s.as_row(meeting_id, owner_id, run_id)
            row["id"] = f"seg-{s.segment_index}"
            self.tables["transcript_segments"].append(row)
            idx[s.segment_index] = row["id"]
        return idx

    def map_speakers(
        self, *, meeting_id, owner_id, run_id, speaker_labels, known_names
    ):
        mapping = {}
        for label in speaker_labels:
            pid = f"p-{label}"
            self.tables["participants"].append(
                {"id": pid, "speaker_label": label, "display_name": friendly_speaker_name(label)}
            )
            mapping[label] = pid
        return mapping

    def save_intelligence(
        self, *, meeting_id, owner_id, run_id, intel, index_to_id,
        generation_version, model, speaker_to_participant,
    ):
        for t in ("summaries", "key_points", "decisions", "action_items", "questions"):
            self.tables[t] = [r for r in self.tables[t] if r.get("run_id") != run_id]
        self.tables["summaries"].append(
            {"run_id": run_id, "summary": intel.summary, "generation_version": generation_version}
        )
        for k in intel.key_points:
            ev = [index_to_id[i] for i in k.evidence_segment_indices if i in index_to_id]
            self.tables["key_points"].append(
                {"run_id": run_id, "content": k.text, "evidence_segment_ids": ev}
            )
        for d in intel.decisions:
            ev = [index_to_id[i] for i in d.evidence_segment_indices if i in index_to_id]
            self.tables["decisions"].append(
                {"run_id": run_id, "description": d.text, "evidence_segment_ids": ev}
            )
        for a in intel.action_items:
            self.tables["action_items"].append(
                {"run_id": run_id, "description": a.task, "status": "open"}
            )
        for q in intel.open_questions:
            self.tables["questions"].append(
                {"run_id": run_id, "question": q.question, "resolved": q.resolved}
            )


@pytest.fixture()
def settings():
    return Settings(
        supabase_url="http://localhost",
        supabase_service_role_key="x",
        worker_fake_providers=True,
    )


@pytest.fixture()
def meeting():
    return {
        "id": "m-1",
        "owner_id": "u-1",
        "status": "queued",
        "source_type": "upload",
        "source_file_path": "u-1/m-1/file.mp3",
    }


@pytest.fixture()
def job():
    return {
        "id": "j-1",
        "meeting_id": "m-1",
        "status": "queued",
        "attempts": 0,
        "max_attempts": 3,
        "run_id": "run-1",
    }


@pytest.fixture()
def pipeline(settings, meeting, job, monkeypatch):
    store = MemoryStore(meeting, job)
    monkeypatch.setattr(
        ffmpeg,
        "prepare_audio_chunks",
        lambda *a, **k: (
            [ffmpeg.AudioChunk(path="x", offset_seconds=0.0)],
            ffmpeg.MediaInfo(14.0, True, False, "mp3"),
        ),
    )
    return Pipeline(
        settings=settings,
        store=store,
        transcription=FakeTranscriptionProvider(),
        intelligence=FakeIntelligenceProvider(),
    ), store


def msg():
    return QueueMessage(
        msg_id=1, read_ct=1, meeting_id="m-1", job_id="j-1",
        job_type="process_meeting",
    )


class TestPipelineSuccess:
    def test_full_run(self, pipeline):
        pipe, store = pipeline
        action, delay = pipe.process(msg())
        assert action == "done"

        meeting = store.get_meeting("m-1")
        assert meeting["status"] == "completed"
        assert meeting["current_run_id"] == "run-1"
        assert meeting["duration_seconds"] == 14

        job = store.get_job("j-1")
        assert job["status"] == "succeeded"
        assert job["attempts"] == 1

        segs = store.tables["transcript_segments"]
        assert len(segs) == 4
        assert {s["speaker_label"] for s in segs} == {"A", "B"}

        assert store.tables["summaries"][0]["summary"].startswith("The team agreed")
        assert store.tables["decisions"]
        assert store.tables["action_items"]
        assert store.tables["questions"]
        # Evidence ids must resolve to real segment rows.
        kp = store.tables["key_points"][0]
        assert kp["evidence_segment_ids"] == ["seg-0"]

        stages = [e["stage"] for e in store.tables["processing_job_events"]]
        assert "transcribe" in stages and "intelligence" in stages

    def test_duplicate_delivery_is_idempotent(self, pipeline):
        pipe, store = pipeline
        pipe.process(msg())
        # Second delivery: job already terminal — no duplicate writes.
        action, _ = pipe.process(msg())
        assert action == "done"
        assert len(store.tables["transcript_segments"]) == 4
        assert len(store.tables["summaries"]) == 1

    def test_participants_created_per_speaker(self, pipeline):
        pipe, store = pipeline
        pipe.process(msg())
        names = {p["display_name"] for p in store.tables["participants"]}
        assert names == {"Speaker 1", "Speaker 2"}


class TestPipelineFailures:
    def test_missing_source_is_fatal(self, pipeline, monkeypatch):
        pipe, store = pipeline
        store.get_meeting("m-1")["source_file_path"] = None
        action, delay = pipe.process(msg())
        assert action == "failed"
        assert store.get_meeting("m-1")["status"] == "failed"
        assert store.get_job("j-1")["error_code"] == "missing_source"

    def test_retryable_error_requeues(self, settings, meeting, job, monkeypatch):
        store = MemoryStore(meeting, job)

        class FlakyTranscriber(FakeTranscriptionProvider):
            def transcribe(self, chunk, known_speakers):
                from app.pipeline.errors import ProviderError

                raise ProviderError("rate limited")

        monkeypatch.setattr(
            ffmpeg,
            "prepare_audio_chunks",
            lambda *a, **k: (
                [ffmpeg.AudioChunk(path="x", offset_seconds=0.0)],
                ffmpeg.MediaInfo(14.0, True, False, "mp3"),
            ),
        )
        pipe = Pipeline(
            settings=settings,
            store=store,
            transcription=FlakyTranscriber(),
            intelligence=FakeIntelligenceProvider(),
        )
        action, delay = pipe.process(msg())
        assert action == "retry"
        assert delay >= 30
        assert store.get_job("j-1")["status"] == "queued"
        assert store.get_meeting("m-1")["status"] == "queued"

    def test_exhausted_attempts_fail_terminally(self, settings, meeting, job, monkeypatch):
        job["attempts"] = 2
        job["max_attempts"] = 3
        store = MemoryStore(meeting, job)

        class AlwaysFail(FakeTranscriptionProvider):
            def transcribe(self, chunk, known_speakers):
                from app.pipeline.errors import ProviderError

                raise ProviderError("still failing")

        monkeypatch.setattr(
            ffmpeg,
            "prepare_audio_chunks",
            lambda *a, **k: (
                [ffmpeg.AudioChunk(path="x", offset_seconds=0.0)],
                ffmpeg.MediaInfo(14.0, True, False, "mp3"),
            ),
        )
        pipe = Pipeline(
            settings=settings,
            store=store,
            transcription=AlwaysFail(),
            intelligence=FakeIntelligenceProvider(),
        )
        # attempts becomes 3 after claim; max is 3 → terminal.
        action, _ = pipe.process(msg())
        assert action == "failed"
        assert store.get_meeting("m-1")["status"] == "failed"
