"""Worker configuration. All settings come from environment variables;
required secrets raise at startup rather than at first use."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    worker_env: str = "development"
    worker_version: str = "0.1.0"

    supabase_url: str
    supabase_service_role_key: str
    supabase_storage_bucket: str = "meeting-recordings"
    queue_name: str = "meeting-processing"

    openai_api_key: str = ""
    openai_transcription_model: str = "gpt-4o-transcribe-diarize"
    openai_intelligence_model: str = "gpt-4o-mini"

    max_job_attempts: int = 4
    worker_concurrency: int = 1
    worker_poll_interval_seconds: float = 5.0
    queue_visibility_timeout_seconds: int = 900

    temp_dir: str | None = None
    transcribe_chunk_seconds: int = 600
    intelligence_chunk_chars: int = 60_000
    # Provider request ceiling used to decide when chunking is required.
    transcribe_max_file_bytes: int = 24 * 1024 * 1024

    # Development/testing escape hatch. Never enabled implicitly — it must be
    # set explicitly and is rejected when worker_env == "production".
    worker_fake_providers: bool = False

    def validate_runtime(self) -> None:
        if self.worker_env == "production" and self.worker_fake_providers:
            raise ValueError("WORKER_FAKE_PROVIDERS must not be enabled in production")
        if not self.worker_fake_providers and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when fake providers are off")


def load_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.validate_runtime()
    return settings
