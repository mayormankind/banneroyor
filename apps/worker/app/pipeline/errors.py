"""Error taxonomy for the processing pipeline.

- FatalError: the job can never succeed (bad media, bad message, auth/config).
  Recorded with a user-safe code + message, the job goes to a terminal state.
- RetryableError: transient conditions (network, provider 429/5xx, timeouts,
  transient DB errors). The message is postponed with bounded backoff.
"""


class PipelineError(Exception):
    error_code = "pipeline_error"
    safe_message = "Processing failed."

    def __init__(self, safe_message: str | None = None, *, detail: str | None = None):
        super().__init__(safe_message or self.safe_message)
        if safe_message:
            self.safe_message = safe_message
        self.detail = detail


class FatalError(PipelineError):
    error_code = "fatal"


class RetryableError(PipelineError):
    error_code = "retryable"


class MediaValidationError(FatalError):
    error_code = "invalid_media"
    safe_message = (
        "The recording could not be processed. "
        "It may be corrupt or use an unsupported format."
    )


class MissingSourceError(FatalError):
    error_code = "missing_source"
    safe_message = "The uploaded recording was not found."


class InvalidMessageError(FatalError):
    error_code = "invalid_message"
    safe_message = "The processing request was invalid."


class ProviderError(RetryableError):
    error_code = "provider_error"
    safe_message = "The transcription/analysis provider returned an error."


def classify_provider_status(status_code: int | None) -> type[PipelineError]:
    """Map an HTTP status to the right error class."""
    if status_code is None:
        return RetryableError
    if status_code in (408, 409, 429) or status_code >= 500:
        return RetryableError
    if status_code in (401, 403):
        return FatalError
    return FatalError
