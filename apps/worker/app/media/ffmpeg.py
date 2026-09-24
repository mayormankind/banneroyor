"""FFmpeg-backed media inspection, audio normalization, and silence-aware
chunking. Everything here is pure subprocess + file IO so it is easy to test
and reason about."""

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from app.pipeline.errors import MediaValidationError, RetryableError

log = logging.getLogger(__name__)

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

PROBE_TIMEOUT = 60
TRANSCODE_TIMEOUT = 3600


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    has_audio: bool
    has_video: bool
    format_name: str


@dataclass(frozen=True)
class AudioChunk:
    path: str
    offset_seconds: float


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaValidationError(
            "Media tools are unavailable on this worker.", detail=str(exc)
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RetryableError(
            "Media tool timed out.", detail=f"timeout running {cmd[0]}"
        ) from exc


def probe(path: str) -> MediaInfo:
    """Inspect a media file. Raises MediaValidationError for corrupt/unsupported
    input or files without an audio stream."""
    res = _run(
        [
            FFPROBE,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            path,
        ],
        PROBE_TIMEOUT,
    )
    if res.returncode != 0:
        raise MediaValidationError(detail=res.stderr[-500:])
    try:
        meta = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise MediaValidationError(detail="ffprobe returned unparseable output") from exc

    streams = meta.get("streams") or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(
        s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0
        for s in streams
    )
    if not has_audio:
        raise MediaValidationError(
            "The file has no audio track to transcribe.",
            detail=f"no audio stream in {os.path.basename(path)}",
        )
    try:
        duration = float(meta["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaValidationError(detail="missing duration in ffprobe output") from exc
    if duration <= 0:
        raise MediaValidationError(detail="non-positive media duration")
    return MediaInfo(
        duration_seconds=duration,
        has_audio=has_audio,
        has_video=has_video,
        format_name=str(meta.get("format", {}).get("format_name", "")),
    )


def normalize_audio(src: str, dst_dir: str) -> str:
    """Extract/normalize audio to 16 kHz mono MP3 — compact and accepted by the
    transcription provider. Returns the output path."""
    out = os.path.join(dst_dir, "normalized.mp3")
    res = _run(
        [
            FFMPEG, "-y", "-v", "error",
            "-i", src,
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "48k",
            "-f", "mp3",
            out,
        ],
        TRANSCODE_TIMEOUT,
    )
    if res.returncode != 0:
        raise MediaValidationError(
            "Audio extraction failed.", detail=res.stderr[-500:]
        )
    return out


def detect_silences(
    path: str, noise_db: int = -35, min_duration: float = 0.4
) -> list[tuple[float, float]]:
    """Return (start, end) silence intervals via ffmpeg silencedetect."""
    res = _run(
        [
            FFMPEG, "-hide_banner", "-nostats",
            "-i", path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f", "null", "-",
        ],
        TRANSCODE_TIMEOUT,
    )
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in res.stderr.splitlines():
        m_start = re.search(r"silence_start: ([\d.]+)", line)
        if m_start:
            start = float(m_start.group(1))
        m_end = re.search(r"silence_end: ([\d.]+)", line)
        if m_end and start is not None:
            silences.append((start, float(m_end.group(1))))
            start = None
    return silences


def choose_split_points(
    duration_seconds: float,
    chunk_seconds: int,
    silences: list[tuple[float, float]],
    search_window_seconds: float = 45.0,
) -> list[float]:
    """Pick split times at silence midpoints nearest each ideal boundary.
    Falls back to the exact boundary when no silence is nearby."""
    points: list[float] = []
    boundary = float(chunk_seconds)
    while boundary < duration_seconds - 1.0:
        best: float | None = None
        best_dist = float("inf")
        for s_start, s_end in silences:
            mid = (s_start + s_end) / 2
            if mid <= (points[-1] if points else 0):
                continue
            dist = abs(mid - boundary)
            if dist <= search_window_seconds and dist < best_dist:
                best = mid
                best_dist = dist
        points.append(best if best is not None else boundary)
        boundary += chunk_seconds
    return points


def split_audio(
    path: str, split_points: list[float], dst_dir: str, duration: float
) -> list[AudioChunk]:
    """Split normalized audio at the given offsets into separate files."""
    if not split_points:
        return [AudioChunk(path=path, offset_seconds=0.0)]
    bounds = [0.0, *split_points, duration]
    chunks: list[AudioChunk] = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        out = os.path.join(dst_dir, f"chunk_{i:03d}.mp3")
        res = _run(
            [
                FFMPEG, "-y", "-v", "error",
                "-ss", f"{start:.3f}",
                "-to", f"{end:.3f}",
                "-i", path,
                "-c", "copy",
                out,
            ],
            TRANSCODE_TIMEOUT,
        )
        if res.returncode != 0:
            raise RetryableError(
                "Audio chunking failed.", detail=res.stderr[-500:]
            )
        chunks.append(AudioChunk(path=out, offset_seconds=start))
    return chunks


def prepare_audio_chunks(
    src_path: str,
    work_dir: str,
    *,
    chunk_seconds: int,
    max_file_bytes: int,
) -> tuple[list[AudioChunk], MediaInfo]:
    """Full media stage: probe → normalize → chunk when required by size or
    duration. Returns chunk list (with timeline offsets) and media info."""
    info = probe(src_path)
    normalized = normalize_audio(src_path, work_dir)
    size = os.path.getsize(normalized)

    if info.duration_seconds <= chunk_seconds and size <= max_file_bytes:
        return [AudioChunk(path=normalized, offset_seconds=0.0)], info

    silences = detect_silences(normalized)
    # A tighter duration target may still be needed for size limits; scale the
    # effective chunk length down if the normalized file is too large.
    effective_chunk = chunk_seconds
    if size > max_file_bytes:
        ratio = max_file_bytes / size
        effective_chunk = max(60, int(chunk_seconds * ratio * 0.9))
    points = choose_split_points(info.duration_seconds, effective_chunk, silences)
    chunks = split_audio(normalized, points, work_dir, info.duration_seconds)
    log.info("audio split into %d chunks", len(chunks))
    return chunks, info


def make_work_dir(temp_root: str | None) -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(
        prefix="banner-recall-", dir=temp_root or None
    )
