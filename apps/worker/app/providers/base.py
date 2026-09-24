"""Provider boundaries. Pipeline code depends on these protocols only."""

from dataclasses import dataclass, field
from typing import Protocol

from app.intelligence.schema import MeetingIntelligence
from app.transcript.model import NormalizedSegment, TranscriptChunk


@dataclass(frozen=True)
class KnownSpeakerRef:
    """A known-speaker reference clip for provider-side identity hints."""

    name: str
    audio_bytes: bytes
    mime_type: str = "audio/mpeg"


@dataclass(frozen=True)
class TranscriptionResult:
    segments: list[NormalizedSegment]
    duration_seconds: float | None
    model: str
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IntelligenceResult:
    data: MeetingIntelligence
    model: str
    prompt_version: str
    usage: dict = field(default_factory=dict)


class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        chunk: TranscriptChunk,
        known_speakers: list[KnownSpeakerRef],
    ) -> TranscriptionResult: ...


class IntelligenceProvider(Protocol):
    def analyze(
        self, transcript: str, *, prompt_version: str
    ) -> IntelligenceResult: ...
