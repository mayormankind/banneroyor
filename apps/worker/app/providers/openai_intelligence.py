"""OpenAI structured meeting-intelligence adapter. Uses the Responses API with
strict schema parsing (pydantic) so malformed output fails validation rather
than reaching the database."""

import logging
from pathlib import Path

from openai import APIError, APIStatusError, OpenAI, RateLimitError

from app.intelligence.schema import MeetingIntelligence
from app.pipeline.errors import (
    FatalError,
    ProviderError,
    RetryableError,
    classify_provider_status,
)
from app.providers.base import IntelligenceResult

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSION = "intelligence_v1"


def load_prompt(version: str = PROMPT_VERSION) -> str:
    path = PROMPTS_DIR / f"{version}.md"
    return path.read_text(encoding="utf-8")


class OpenAIIntelligenceProvider:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self._instructions = load_prompt()

    def analyze(
        self, transcript: str, *, prompt_version: str = PROMPT_VERSION
    ) -> IntelligenceResult:
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self._instructions,
                input=f"Transcript:\n{transcript}",
                text_format=MeetingIntelligence,
            )
        except RateLimitError as exc:
            raise RetryableError(
                "Analysis rate-limited; will retry.", detail=str(exc)
            ) from exc
        except APIStatusError as exc:
            cls = classify_provider_status(exc.status_code)
            raise cls("Analysis provider request failed.", detail=str(exc)) from exc
        except APIError as exc:
            raise ProviderError(detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise FatalError(
                "Analysis output failed schema validation.", detail=str(exc)
            ) from exc

        parsed = response.output_parsed
        if parsed is None:
            # Bounded repair: one retry asking the model to fix its output.
            try:
                retry = self.client.responses.parse(
                    model=self.model,
                    instructions=self._instructions
                    + "\n\nYour previous output was invalid. "
                    "Return ONLY valid data matching the schema.",
                    input=f"Transcript:\n{transcript}",
                    text_format=MeetingIntelligence,
                )
            except Exception as exc:
                raise FatalError(
                    "Analysis output failed schema validation after retry.",
                    detail=str(exc),
                ) from exc
            parsed = retry.output_parsed
            if parsed is None:
                raise FatalError(
                    "Analysis returned no structured output.",
                    detail="output_parsed was None after repair attempt",
                )

        usage = {}
        if getattr(response, "usage", None):
            usage = (
                response.usage.model_dump()
                if hasattr(response.usage, "model_dump")
                else dict(response.usage)
            )

        return IntelligenceResult(
            data=parsed,
            model=self.model,
            prompt_version=prompt_version,
            usage=usage,
        )
