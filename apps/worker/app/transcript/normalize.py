"""Normalize provider transcription output into internal segments and merge
chunked results back onto the meeting timeline."""

from app.transcript.model import NormalizedSegment


def normalize_provider_segments(
    raw_segments: list[dict],
    *,
    offset_seconds: float = 0.0,
    index_start: int = 0,
) -> list[NormalizedSegment]:
    """Convert provider segment dicts ({speaker, start, end, text}) to
    normalized millisecond segments. Invalid/empty segments are dropped."""
    out: list[NormalizedSegment] = []
    for raw in raw_segments:
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(raw.get("text") or "").strip()
        if not text or end <= start:
            continue
        speaker = raw.get("speaker")
        out.append(
            NormalizedSegment(
                segment_index=index_start + len(out),
                start_ms=round((offset_seconds + start) * 1000),
                end_ms=round((offset_seconds + end) * 1000),
                speaker_label=str(speaker) if speaker else None,
                text=text,
            )
        )
    return out


def merge_chunk_segments(chunks: list[list[NormalizedSegment]]) -> list[NormalizedSegment]:
    """Concatenate per-chunk segments and re-index on the global timeline.
    Chunk boundaries are hard splits so no overlap deduplication is required;
    any accidental zero-length/duplicate edges are still filtered out."""
    merged: list[NormalizedSegment] = []
    seen: set[tuple[int, int, str]] = set()
    for segs in chunks:
        for s in segs:
            key = (s.start_ms, s.end_ms, s.text)
            if key in seen:
                continue
            seen.add(key)
            merged.append(s)
    merged.sort(key=lambda s: (s.start_ms, s.end_ms))
    return [
        NormalizedSegment(
            segment_index=i,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            speaker_label=s.speaker_label,
            text=s.text,
        )
        for i, s in enumerate(merged)
    ]


def render_transcript_for_prompt(segments: list[NormalizedSegment]) -> str:
    """Render transcript with stable segment indices for the intelligence
    prompt. The model cites these indices as evidence references."""

    def ts(ms: int) -> str:
        total = ms // 1000
        return f"{total // 60:02d}:{total % 60:02d}"

    lines = []
    for s in segments:
        speaker = s.speaker_label or "Unknown"
        lines.append(f"[{s.segment_index}] {ts(s.start_ms)} {speaker}: {s.text}")
    return "\n".join(lines)


def chunk_segments_by_chars(
    segments: list[NormalizedSegment], max_chars: int
) -> list[list[NormalizedSegment]]:
    """Split a long transcript into prompt-sized chunks on segment boundaries
    (never mid-segment). Evidence indices stay global — the model sees the
    original segment_index values."""
    chunks: list[list[NormalizedSegment]] = []
    current: list[NormalizedSegment] = []
    size = 0
    for s in segments:
        line_len = len(s.text) + 40
        if current and size + line_len > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(s)
        size += line_len
    if current:
        chunks.append(current)
    return chunks
