"""Generate a two-speaker test meeting recording via OpenAI TTS.

Produces interleaved voice clips and concatenates them with ffmpeg into
one MP3 that exercises diarization end-to-end.

Usage (worker venv):
    FFMPEG_BIN=<path> python scripts/make_test_audio.py out.mp3
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]

# (voice, line) — a tiny meeting with a decision, an action item, a question.
SCRIPT = [
    ("alloy", "Let's review the plan for the reporting dashboard."),
    (
        "onyx",
        "Cursor pagination is the right approach for the API. "
        "I will implement it by next Friday.",
    ),
    ("alloy", "Agreed, we are shipping cursor pagination. Decision made."),
    ("onyx", "One question remains, should the endpoint expose page size as a parameter?"),
    ("alloy", "Good question. Let us leave it open for now and revisit next week."),
]


def env_get(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "test_meeting.mp3")
    client = OpenAI(api_key=env_get("OPENAI_API_KEY"))
    ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg")

    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (voice, line) in enumerate(SCRIPT):
            p = Path(td) / f"part_{i:02d}.mp3"
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts", voice=voice, input=line
            )
            p.write_bytes(resp.read())
            parts.append(p)
            print(f"generated {p.name}: {line[:50]}")

        concat = Path(td) / "concat.txt"
        concat.write_text("".join(f"file '{p}'\n" for p in parts))
        res = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat), "-c", "copy", str(out)],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            # MP3 concat demuxer can misbehave; fall back to re-encode.
            res = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", str(concat), "-ar", "16000", "-ac", "1", str(out)],
                capture_output=True, text=True,
            )
            if res.returncode != 0:
                print(res.stderr)
                return 1

    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
