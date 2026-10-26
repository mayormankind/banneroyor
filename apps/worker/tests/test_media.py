import json
import subprocess

import pytest

from app.media import ffmpeg
from app.pipeline.errors import MediaValidationError


def _proc(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


PROBE_NO_DURATION = json.dumps(
    {
        "format": {"format_name": "matroska,webm"},  # no "duration" key
        "streams": [{"codec_type": "audio"}],
    }
)


class TestProbe:
    def test_missing_duration_falls_back_to_decode(self, monkeypatch):
        """MediaRecorder WebM has no duration metadata — probe must measure
        by decoding instead of raising (live e2e hit this for every part)."""
        calls = []

        def fake_run(cmd, timeout):
            calls.append(cmd[0])
            if "ffprobe" in cmd[0]:
                return _proc(stdout=PROBE_NO_DURATION)
            return _proc(stderr="frame= 100 time=00:01:23.45 bitrate=N/A")

        monkeypatch.setattr(ffmpeg, "_run", fake_run)
        info = ffmpeg.probe("part.webm")
        assert info.duration_seconds == 83.45
        assert calls == [ffmpeg.FFPROBE, ffmpeg.FFMPEG]

    def test_decode_without_timestamp_still_fails(self, monkeypatch):
        def fake_run(cmd, timeout):
            if "ffprobe" in cmd[0]:
                return _proc(stdout=PROBE_NO_DURATION)
            return _proc(stderr="no timing info")

        monkeypatch.setattr(ffmpeg, "_run", fake_run)
        with pytest.raises(MediaValidationError):
            ffmpeg.probe("part.webm")

    def test_no_audio_rejected(self, monkeypatch):
        meta = json.dumps(
            {
                "format": {"duration": "5.0"},
                "streams": [{"codec_type": "video"}],
            }
        )
        monkeypatch.setattr(ffmpeg, "_run", lambda c, t: _proc(stdout=meta))
        with pytest.raises(MediaValidationError):
            ffmpeg.probe("video-only.mp4")
