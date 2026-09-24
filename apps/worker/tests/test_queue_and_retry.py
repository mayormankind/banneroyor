import pytest

from app.media.ffmpeg import choose_split_points
from app.pipeline.errors import (
    FatalError,
    RetryableError,
    classify_provider_status,
)
from app.pipeline.orchestrator import backoff_seconds
from app.queue.adapter import parse_message


class TestParseMessage:
    def test_valid(self):
        msg = parse_message(
            {"meeting_id": "m1", "job_id": "j1", "job_type": "process_meeting"},
            msg_id=5,
            read_ct=1,
        )
        assert msg.meeting_id == "m1"
        assert msg.job_type == "process_meeting"

    def test_missing_ids_rejected(self):
        with pytest.raises(ValueError):
            parse_message({"job_type": "process_meeting"}, 1, 1)

    def test_unknown_job_type_rejected(self):
        with pytest.raises(ValueError):
            parse_message(
                {"meeting_id": "m", "job_id": "j", "job_type": "delete_all"},
                1,
                1,
            )


class TestBackoff:
    def test_monotonic_and_bounded(self):
        d1 = backoff_seconds(1)
        d2 = backoff_seconds(2)
        d3 = backoff_seconds(3)
        assert d1 >= 30 and d2 >= 60 and d3 >= 120
        assert backoff_seconds(100) <= 900

    def test_jitter_within_range(self):
        for _ in range(50):
            d = backoff_seconds(1)
            assert 30 <= d <= 60


class TestProviderStatusClassification:
    def test_retryable_statuses(self):
        for code in (408, 409, 429, 500, 502, 503):
            assert classify_provider_status(code) is RetryableError

    def test_fatal_statuses(self):
        for code in (400, 401, 403, 404, 422):
            assert classify_provider_status(code) is FatalError


class TestSplitPoints:
    def test_prefers_silence_near_boundary(self):
        silences = [(595.0, 597.0), (1195.0, 1197.0)]
        pts = choose_split_points(1800, 600, silences)
        assert pts[0] == pytest.approx(596.0)
        assert pts[1] == pytest.approx(1196.0)

    def test_falls_back_to_exact_boundary(self):
        pts = choose_split_points(1800, 600, [])
        assert pts == [600.0, 1200.0]

    def test_ignores_distant_silence(self):
        silences = [(100.0, 101.0)]
        pts = choose_split_points(1200, 600, silences)
        assert pts == [600.0]

    def test_no_split_for_short_audio(self):
        assert choose_split_points(500, 600, []) == []
