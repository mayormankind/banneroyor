"""OpenAI diarized transcription adapter (gpt-4o-transcribe-diarize).

Verified against the current Transcription API contract:
- model: gpt-4o-transcribe-diarize
- response_format="diarized_json" returns TranscriptionDiarized with
  segments[] = {id, speaker, start, end, text, type}
- chunking_strategy="auto" (or server_vad config) is required for audio >30s
- known_speaker_names[] / known_speaker_references[] accept up to 4 speaker
  name + reference-audio (data-URL) pairs
"""

import base64
import logging
from typing import Any

from openai import APIError, APIStatusError, OpenAI, RateLimitError

from app.pipeline.errors import (
    MediaValidationError,
    ProviderError,
    RetryableError,
    classify_provider_status,
)
from app.providers.base import KnownSpeakerRef, TranscriptionResult
from app.transcript.model import TranscriptChunk
from app.transcript.normalize import normalize_provider_segments

log = logging.getLogger(__name__)


def _data_url(ref: KnownSpeakerRef) -> str:
    return f"data:{ref.mime_type};base64,{base64.b64encode(ref.audio_bytes).decode()}"


class OpenAITranscriptionProvider:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def transcribe(
        self, chunk: TranscriptChunk, known_speakers: list[KnownSpeakerRef]
    ) -> TranscriptionResult:
        extra: dict[str, Any] = {}
        if known_speakers:
            # The API accepts up to 4 known-speaker references.
            refs = known_speakers[:4]
            extra["known_speaker_names"] = [r.name for r in refs]
            extra["known_speaker_references"] = [_data_url(r) for r in refs]

        try:
            with open(chunk.path, "rb") as fh:
                result = self.client.audio.transcriptions.create(
                    model=self.model,
                    file=fh,
                    response_format="diarized_json",
                    chunking_strategy="auto",
                    **extra,
                )
        except RateLimitError as exc:
            raise RetryableError(
                "Transcription rate-limited; will retry.", detail=str(exc)
            ) from exc
        except APIStatusError as exc:
            cls = classify_provider_status(exc.status_code)
            raise cls(
                "Transcription provider request failed.", detail=str(exc)
            ) from exc
        except APIError as exc:
            raise ProviderError(detail=str(exc)) from exc
        except OSError as exc:
            raise RetryableError(
                "Could not read audio chunk.", detail=str(exc)
            ) from exc

        raw_segments: list[dict] = []
        for seg in getattr(result, "segments", None) or []:
            raw_segments.append(
                {
                    "speaker": getattr(seg, "speaker", None)
                    or (seg.get("speaker") if isinstance(seg, dict) else None),
                    "start": getattr(seg, "start", None)
                    if not isinstance(seg, dict)
                    else seg.get("start"),
                    "end": getattr(seg, "end", None)
                    if not isinstance(seg, dict)
                    else seg.get("end"),
                    "text": getattr(seg, "text", None)
                    if not isinstance(seg, dict)
                    else seg.get("text"),
                }
            )

        if not raw_segments and not getattr(result, "text", None):
            raise MediaValidationError(
                "The recording contains no transcribable speech.",
                detail="provider returned empty transcript",
            )

        usage: dict[str, Any] = {}
        if getattr(result, "usage", None):
            usage = (
                result.usage.model_dump()
                if hasattr(result.usage, "model_dump")
                else dict(result.usage)
            )

        return TranscriptionResult(
            segments=normalize_provider_segments(
                raw_segments, offset_seconds=chunk.offset_seconds
            ),
            duration_seconds=getattr(result, "duration", None),
            model=self.model,
            usage=usage,
        )
