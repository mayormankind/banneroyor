"""Normalized internal transcript model — the shape persisted to Postgres."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator


@dataclass(frozen=True)
class TranscriptChunk:
    """A media chunk to transcribe, with its offset on the meeting timeline."""

    path: str
    offset_seconds: float


class NormalizedSegment(BaseModel):
    """Provider-independent transcript segment in milliseconds."""

    segment_index: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str | None = None
    text: str

    @field_validator("end_ms")
    @classmethod
    def end_after_start(cls, v: int, info):
        start = info.data.get("start_ms")
        if start is not None and v < start:
            raise ValueError("end_ms must not precede start_ms")
        return v

    def as_row(self, meeting_id: str, owner_id: str, run_id: str) -> dict:
        return {
            "meeting_id": meeting_id,
            "owner_id": owner_id,
            "run_id": run_id,
            "segment_index": self.segment_index,
            "speaker_label": self.speaker_label,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "source": "provider",
        }
