from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["openrouter", "xai", "openai"]


class Settings(BaseSettings):
    youtube_api_key: str = ""
    youtube_region_code: str = "US"
    youtube_relevance_language: str = "en"
    youtube_candidate_count: int = Field(default=12, ge=3, le=30)
    youtube_top_video_count: int = Field(default=3, ge=1, le=3)
    comments_per_video: int = Field(default=15, ge=1, le=50)

    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_app_url: str = "http://localhost:3000"
    openrouter_app_name: str = "ReviewLens POC"

    xai_api_key: str = ""
    xai_model: str = "grok-4.5"

    openai_api_key: str = ""
    openai_model: str = ""

    ai_primary_provider: ProviderName = "openrouter"
    ai_aggregator_provider: str = "auto"
    ai_max_output_tokens: int = Field(default=3500, ge=500, le=12000)
    ai_temperature: float = Field(default=0.1, ge=0.0, le=1.0)

    max_transcript_chars_per_video: int = Field(default=45_000, ge=5_000, le=150_000)
    max_comment_chars_per_video: int = Field(default=10_000, ge=1_000, le=50_000)

    app_env: str = "development"
    backend_cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.backend_cors_origins.split(",") if item.strip()]

    def provider_available(self, provider: str) -> bool:
        if provider == "openrouter":
            return bool(self.openrouter_api_key)
        if provider == "xai":
            return bool(self.xai_api_key)
        if provider == "openai":
            return bool(self.openai_api_key and self.openai_model)
        return False

    def model_for(self, provider: str) -> str:
        if provider == "openrouter":
            return self.openrouter_model
        if provider == "xai":
            return self.xai_model
        if provider == "openai":
            return self.openai_model
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
