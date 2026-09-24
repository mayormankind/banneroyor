import pytest

from app.transcript.model import NormalizedSegment
from app.transcript.normalize import (
    chunk_segments_by_chars,
    merge_chunk_segments,
    normalize_provider_segments,
    render_transcript_for_prompt,
)


def seg(i, start, end, speaker="A", text="hello"):
    return NormalizedSegment(
        segment_index=i, start_ms=start, end_ms=end, speaker_label=speaker, text=text
    )


class TestNormalizeProviderSegments:
    def test_converts_seconds_to_ms_with_offset(self):
        raw = [{"speaker": "A", "start": 1.5, "end": 3.0, "text": "hi"}]
        out = normalize_provider_segments(raw, offset_seconds=600.0)
        assert out[0].start_ms == 601_500
        assert out[0].end_ms == 603_000
        assert out[0].speaker_label == "A"
        assert out[0].segment_index == 0

    def test_drops_empty_and_invalid(self):
        raw = [
            {"speaker": "A", "start": 0, "end": 1, "text": ""},
            {"speaker": "A", "start": 2, "end": 1, "text": "bad order"},
            {"speaker": "A", "start": 3, "end": 4, "text": "ok"},
            {"start": "nope", "end": 5, "text": "broken"},
        ]
        out = normalize_provider_segments(raw)
        assert len(out) == 1
        assert out[0].text == "ok"
        assert out[0].segment_index == 0  # re-indexed densely

    def test_index_start_continues(self):
        raw = [{"speaker": "B", "start": 0, "end": 1, "text": "x"}]
        out = normalize_provider_segments(raw, index_start=7)
        assert out[0].segment_index == 7


class TestMergeChunkSegments:
    def test_merges_and_reindexes(self):
        c1 = [seg(0, 0, 4000), seg(1, 4000, 8000)]
        c2 = [seg(0, 600_000, 604_000), seg(1, 604_000, 608_000)]
        merged = merge_chunk_segments([c1, c2])
        assert [s.segment_index for s in merged] == [0, 1, 2, 3]
        assert merged[2].start_ms == 600_000

    def test_deduplicates_identical_segments(self):
        s = seg(0, 0, 4000, text="same")
        merged = merge_chunk_segments([[s], [s]])
        assert len(merged) == 1

    def test_sorts_by_time(self):
        merged = merge_chunk_segments([[seg(0, 5000, 9000)], [seg(0, 0, 4000)]])
        assert merged[0].start_ms == 0


class TestPromptRender:
    def test_renders_indices_and_timestamps(self):
        out = render_transcript_for_prompt([seg(3, 65_000, 66_000, "A", "hi")])
        assert "[3] 01:05 A: hi" in out

    def test_unknown_speaker(self):
        out = render_transcript_for_prompt(
            [NormalizedSegment(segment_index=0, start_ms=0, end_ms=1, speaker_label=None, text="x")]
        )
        assert "Unknown" in out


class TestChunkSegmentsByChars:
    def test_splits_on_segment_boundaries(self):
        segs = [seg(i, i * 1000, i * 1000 + 500, text="x" * 100) for i in range(10)]
        chunks = chunk_segments_by_chars(segs, max_chars=400)
        assert len(chunks) >= 2
        # Every segment appears exactly once, order preserved.
        flat = [s for c in chunks for s in c]
        assert [s.segment_index for s in flat] == list(range(10))

    def test_single_chunk_when_small(self):
        segs = [seg(0, 0, 1000, text="short")]
        assert len(chunk_segments_by_chars(segs, 60_000)) == 1


class TestSegmentValidation:
    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError):
            NormalizedSegment(segment_index=0, start_ms=500, end_ms=100, text="x")

    def test_negative_start_rejected(self):
        with pytest.raises(ValueError):
            NormalizedSegment(segment_index=0, start_ms=-1, end_ms=100, text="x")
