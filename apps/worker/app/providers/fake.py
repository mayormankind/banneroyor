"""Deterministic fake providers for tests and local development
(WORKER_FAKE_PROVIDERS=true). Never used in production — settings refuse to
load with fakes enabled when WORKER_ENV=production."""

from app.intelligence.schema import (
    ActionItemOut,
    DecisionOut,
    KeyPointOut,
    MeetingIntelligence,
    OpenQuestionOut,
)
from app.providers.base import (
    IntelligenceResult,
    KnownSpeakerRef,
    TranscriptionResult,
)
from app.transcript.model import NormalizedSegment, TranscriptChunk


class FakeTranscriptionProvider:
    """Returns a fixed two-speaker conversation regardless of input."""

    model = "fake-transcribe-1"

    def transcribe(
        self, chunk: TranscriptChunk, known_speakers: list[KnownSpeakerRef]
    ) -> TranscriptionResult:
        base_ms = int(chunk.offset_seconds * 1000)
        speaker_a = known_speakers[0].name if known_speakers else "A"
        speaker_b = known_speakers[1].name if len(known_speakers) > 1 else "B"
        script = [
            (speaker_a, "Let's review the pagination plan for the dashboard API."),
            (speaker_b, "Cursor pagination is the right call. I'll implement it."),
            (speaker_a, "Agreed — we ship it next Friday."),
            (speaker_b, "Should the endpoint expose page size as a parameter?"),
        ]
        segments = [
            NormalizedSegment(
                segment_index=i,
                start_ms=base_ms + i * 4000,
                end_ms=base_ms + i * 4000 + 3500,
                speaker_label=speaker,
                text=text,
            )
            for i, (speaker, text) in enumerate(script)
        ]
        return TranscriptionResult(
            segments=segments,
            duration_seconds=(len(script) * 4000) / 1000,
            model=self.model,
            usage={"type": "seconds", "seconds": 14},
        )


class FakeIntelligenceProvider:
    model = "fake-intelligence-1"

    def analyze(
        self, transcript: str, *, prompt_version: str = "fake_v1"
    ) -> IntelligenceResult:
        data = MeetingIntelligence(
            summary="The team agreed to add cursor pagination to the dashboard API.",
            key_points=[
                KeyPointOut(
                    text="The dashboard API needs pagination.",
                    evidence_segment_indices=[0],
                    timestamp_ms=0,
                )
            ],
            decisions=[
                DecisionOut(
                    text="Use cursor pagination for the dashboard endpoint.",
                    speaker_label="B",
                    evidence_segment_indices=[1],
                    timestamp_ms=4000,
                )
            ],
            action_items=[
                ActionItemOut(
                    task="Implement the pagination endpoint.",
                    owner_speaker_label="B",
                    deadline=None,
                    evidence_segment_indices=[1],
                    timestamp_ms=4000,
                )
            ],
            open_questions=[
                OpenQuestionOut(
                    question="Should the endpoint expose page size as a parameter?",
                    resolved=False,
                    evidence_segment_indices=[3],
                    timestamp_ms=12000,
                )
            ],
        )
        return IntelligenceResult(
            data=data,
            model=self.model,
            prompt_version=prompt_version,
            usage={"input_tokens": 1200, "output_tokens": 300},
        )
