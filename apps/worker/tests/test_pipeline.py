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
            "capture_sessions": [],
            "capture_parts": [],
            "live_transcript_segments": [],
            "live_intelligence_items": [],
            "speaker_profiles": [],
        }
        self.client = type("C", (), {"table": lambda s, n: FakeTable(self, n)})()
        self.files: dict[str, bytes] = {}

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
        return self.files.get(path, b"fake-audio-bytes")

    # live-capture persistence (mirrors SupabaseStore semantics)
    def get_capture_session(self, session_id):
        return next(
            (s for s in self.tables["capture_sessions"] if s["id"] == session_id),
            None,
        )

    def list_capture_parts(self, session_id):
        return sorted(
            (p for p in self.tables["capture_parts"] if p["session_id"] == session_id),
            key=lambda p: p["part_index"],
        )

    def commit_capture_sync(
        self, *, session_id, expected_cursor, new_cursor, new_ms_end, segments
    ):
        session = self.get_capture_session(session_id)
        if session is None or session["transcribed_part_index"] != expected_cursor:
            return False
        run_key = expected_cursor + 1
        self.tables["live_transcript_segments"] = [
            s
            for s in self.tables["live_transcript_segments"]
            if not (
                s["session_id"] == session_id and s["run_part_index"] == run_key
            )
        ]
        for s in segments:
            self.tables["live_transcript_segments"].append(
                {"session_id": session_id, "run_part_index": run_key, **s}
            )
        for p in self.tables["capture_parts"]:
            if (
                p["session_id"] == session_id
                and expected_cursor < p["part_index"] <= new_cursor
            ):
                p["status"] = "processed"
        session["transcribed_part_index"] = new_cursor
        session["transcribed_ms_end"] = new_ms_end
        return True

    def complete_capture_sessions(self, meeting_id):
        for s in self.tables["capture_sessions"]:
            if s["meeting_id"] == meeting_id and s["status"] == "uploaded":
                s["status"] = "completed"

    def speaker_profiles_for_owner(self, owner_id):
        return self.tables.get("speaker_profiles", [])

    def list_live_segments(self, session_id):
        return sorted(
            (
                s
                for s in self.tables["live_transcript_segments"]
                if s["session_id"] == session_id
            ),
            key=lambda s: s["start_ms"],
        )

    def commit_capture_intel(
        self, *, session_id, expected_cursor, new_cursor, items
    ):
        session = self.get_capture_session(session_id)
        if session is None or session.get("intel_part_index", -1) != expected_cursor:
            return False
        self.tables["live_intelligence_items"] = [
            i
            for i in self.tables["live_intelligence_items"]
            if i["session_id"] != session_id
        ]
        for it in items:
            self.tables["live_intelligence_items"].append(
                {"session_id": session_id, "run_part_index": new_cursor, **it}
            )
        session["intel_part_index"] = new_cursor
        return True

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
        self, *, meeting_id, owner_id, run_id, speaker_labels, known_profiles
    ):
        mapping = {}
        for label in speaker_labels:
            pid = f"p-{label}"
            profile_id = known_profiles.get(label)
            self.tables["participants"].append(
                {
                    "id": pid,
                    "speaker_label": label,
                    "display_name": label
                    if profile_id
                    else friendly_speaker_name(label),
                    "speaker_profile_id": profile_id,
                }
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

    def test_known_speaker_profile_links_participant(self, pipeline):
        """A profile with an enrolled clip is sent as a provider hint; when
        the provider confirms the name, the participant and segments link to
        the speaker_profiles row."""
        pipe, store = pipeline
        store.tables["speaker_profiles"].append(
            {
                "id": "sp-1",
                "display_name": "Mayowa",
                "reference_storage_path": "u-1/speaker-refs/sp-1/clip.webm",
            }
        )
        store.files["u-1/speaker-refs/sp-1/clip.webm"] = b"ref-audio"
        action, _ = pipe.process(msg())
        assert action == "done"

        part = next(
            p for p in store.tables["participants"]
            if p["speaker_label"] == "Mayowa"
        )
        assert part["display_name"] == "Mayowa"
        assert part["speaker_profile_id"] == "sp-1"

    def test_browser_source_processes_same_as_upload(self, pipeline):
        """Any source_type with a stored file runs the same pipeline."""
        pipe, store = pipeline
        store.get_meeting("m-1")["source_type"] = "browser"
        action, _ = pipe.process(msg())
        assert action == "done"
        assert store.get_meeting("m-1")["status"] == "completed"


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


def sync_msg(session_id: str = "s-1") -> QueueMessage:
    return QueueMessage(
        msg_id=1,
        read_ct=1,
        meeting_id="m-1",
        job_type="capture_sync",
        capture_session_id=session_id,
    )


@pytest.fixture()
def live_env(settings, meeting, job, monkeypatch, tmp_path):
    """Worker + a capture session with uploaded parts; ffmpeg is faked so the
    concat 'file' reports a known duration and slices are no-ops."""
    settings.temp_dir = str(tmp_path)
    store = MemoryStore(meeting, job)
    store.tables["capture_sessions"].append(
        {
            "id": "s-1",
            "meeting_id": "m-1",
            "owner_id": "u-1",
            "status": "capturing",
            "transcribed_part_index": -1,
            "transcribed_ms_end": 0,
        }
    )
    for i in range(2):
        path = f"u-1/m-1/live/s-1/part-{i:05d}"
        store.files[path] = f"audio-part-{i}".encode()
        store.tables["capture_parts"].append(
            {
                "id": f"part-{i}",
                "session_id": "s-1",
                "meeting_id": "m-1",
                "owner_id": "u-1",
                "part_index": i,
                "storage_path": path,
                "status": "uploaded",
            }
        )
    def _fake_probe(path, *a, **k):
        # Duration scales with the media size: 20s per concatenated part.
        with open(path, "rb") as fh:
            n_parts = fh.read().count(b"audio-part")
        return ffmpeg.MediaInfo(20.0 * n_parts, True, False, "webm")

    def _fake_slice(src, start, end, dst):
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data)
        return dst

    monkeypatch.setattr(ffmpeg, "probe", _fake_probe)
    monkeypatch.setattr(ffmpeg, "extract_audio_slice", _fake_slice)
    return Pipeline(
        settings=settings,
        store=store,
        transcription=FakeTranscriptionProvider(),
        intelligence=FakeIntelligenceProvider(),
    ), store


class TestCaptureSync:
    def test_sync_transcribes_tail_and_advances_cursor(self, live_env):
        pipe, store = live_env
        action, _ = pipe.process(sync_msg())
        assert action == "done"

        session = store.get_capture_session("s-1")
        assert session["transcribed_part_index"] == 1
        assert session["transcribed_ms_end"] == 40000

        segs = store.tables["live_transcript_segments"]
        assert segs and all(s["run_part_index"] == 0 for s in segs)
        assert all(p["status"] == "processed"
                   for p in store.tables["capture_parts"])

    def test_sync_continues_after_cursor_zero(self, live_env):
        """Regression: cursor 0 is falsy — `or -1` reset it, so every sync
        after the first commit no-oped (live e2e stalled at part 1)."""
        pipe, store = live_env
        # First sync only sees part 0 -> cursor lands on 0.
        store.tables["capture_parts"] = [
            p for p in store.tables["capture_parts"] if p["part_index"] == 0
        ]
        assert pipe.process(sync_msg())[0] == "done"
        assert store.get_capture_session("s-1")["transcribed_part_index"] == 0
        # Part 1 arrives later; the next sync must still process it.
        store.tables["capture_parts"].append(
            {
                "id": "part-1",
                "session_id": "s-1",
                "meeting_id": "m-1",
                "owner_id": "u-1",
                "part_index": 1,
                "storage_path": "u-1/m-1/live/s-1/part-00001",
                "status": "uploaded",
            }
        )
        action, _ = pipe.process(sync_msg())
        assert action == "done"
        assert store.get_capture_session("s-1")["transcribed_part_index"] == 1
        runs = {
            s["run_part_index"] for s in store.tables["live_transcript_segments"]
        }
        assert runs == {0, 1}

    def test_sync_no_new_parts_is_noop(self, live_env):
        pipe, store = live_env
        pipe.process(sync_msg())
        n = len(store.tables["live_transcript_segments"])
        action, _ = pipe.process(sync_msg())
        assert action == "done"
        assert len(store.tables["live_transcript_segments"]) == n

    def test_sync_gap_waits_for_missing_part(self, live_env):
        """A gap in part indices must not be skipped — later audio depends on
        the missing bytes for correct timestamps."""
        pipe, store = live_env
        # Remove part 0 → run can't start at index 1.
        store.tables["capture_parts"] = [
            p for p in store.tables["capture_parts"] if p["part_index"] != 0
        ]
        action, _ = pipe.process(sync_msg())
        assert action == "done"
        assert not store.tables["live_transcript_segments"]
        assert store.get_capture_session("s-1")["transcribed_part_index"] == -1

    def test_sync_after_cursor_advances_is_noop(self, live_env):
        """A stale sync message after another sync committed converges as a
        no-op — parts are already processed."""
        pipe, store = live_env
        store.get_capture_session("s-1")["transcribed_part_index"] = 99
        action, _ = pipe.process(sync_msg())
        assert action == "done"

    def test_sync_missing_session_raises(self, live_env):
        from app.pipeline.errors import InvalidMessageError

        pipe, _ = live_env
        with pytest.raises(InvalidMessageError):
            pipe.process(sync_msg("nope"))


def add_part(store, i: int) -> None:
    path = f"u-1/m-1/live/s-1/part-{i:05d}"
    store.files[path] = f"audio-part-{i}".encode()
    store.tables["capture_parts"].append(
        {
            "id": f"part-{i}",
            "session_id": "s-1",
            "meeting_id": "m-1",
            "owner_id": "u-1",
            "part_index": i,
            "storage_path": path,
            "status": "uploaded",
        }
    )


class TestLiveIntel:
    def test_intel_commits_after_interval(self, live_env):
        """Once >= live_intel_part_interval new parts are transcribed, the
        provisional candidate set is written in one run."""
        pipe, store = live_env
        add_part(store, 2)  # sync covers parts 0..2 -> new_cursor 2
        action, _ = pipe.process(sync_msg())
        assert action == "done"

        items = store.tables["live_intelligence_items"]
        assert items
        assert all(i["run_part_index"] == 2 for i in items)
        assert {i["kind"] for i in items} == {
            "key_point", "decision", "action_item", "question",
        }
        assert store.get_capture_session("s-1")["intel_part_index"] == 2

    def test_intel_throttled_below_interval(self, live_env):
        """Only 2 parts synced (interval 3) — no intel call yet."""
        pipe, store = live_env
        pipe.process(sync_msg())
        assert not store.tables["live_intelligence_items"]
        assert store.get_capture_session("s-1").get("intel_part_index") in (
            None,
            -1,
        )

    def test_intel_rerun_replaces_candidate_set(self, live_env):
        """A later intel run replaces the whole provisional set."""
        pipe, store = live_env
        add_part(store, 2)
        pipe.process(sync_msg())
        first = [dict(i) for i in store.tables["live_intelligence_items"]]
        for i in (3, 4, 5):
            add_part(store, i)
        pipe.process(sync_msg())
        items = store.tables["live_intelligence_items"]
        assert items and all(i["run_part_index"] == 5 for i in items)
        assert len(items) == len(first)
        assert store.get_capture_session("s-1")["intel_part_index"] == 5

    def test_intel_failure_does_not_block_transcript_sync(self, live_env):
        """An analysis error must not roll back or retry the committed
        transcript sync."""
        pipe, store = live_env
        add_part(store, 2)

        def boom(*a, **k):
            raise RuntimeError("intelligence backend down")

        pipe.intelligence.analyze = boom  # type: ignore[method-assign]
        action, _ = pipe.process(sync_msg())
        assert action == "done"
        assert store.get_capture_session("s-1")["transcribed_part_index"] == 2
        assert not store.tables["live_intelligence_items"]
