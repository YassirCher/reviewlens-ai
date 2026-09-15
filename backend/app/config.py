from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["openrouter", "xai", "openai"]
ProcessRole = Literal["api", "worker", "scheduler", "migrate"]


class Settings(BaseSettings):
    # Legacy V1 settings remain available until the compatibility phases.
    youtube_api_key: str = ""
    youtube_region_code: str = "US"
    youtube_relevance_language: str = "en"
    youtube_candidate_count: int = Field(default=12, ge=3, le=40)
    youtube_top_video_count: int = Field(default=3, ge=1, le=3)
    comments_per_video: int = Field(default=15, ge=1, le=50)

    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_app_url: str = "http://localhost:3000"
    openrouter_app_name: str = "ReviewLens"
    openrouter_management_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_catalog_refresh_minutes: int = Field(default=15, ge=1, le=1440)
    openrouter_request_timeout_seconds: int = Field(default=120, ge=1, le=600)

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
    app_public_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    backend_cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    raw_llm_content_retention: bool = False

    admin_email: str = ""
    admin_password_hash: str = ""
    # Declared solely so a legacy/plaintext environment value can be rejected
    # explicitly instead of being silently ignored by BaseSettings.
    admin_password: str = Field(default="", exclude=True, repr=False)
    admin_session_cookie: str = "reviewlens_admin_session"
    session_secret: str = ""
    public_token_hash_secret: str = ""
    rate_limit_hash_secret: str = ""
    session_idle_minutes: int = Field(default=60, ge=5, le=1440)
    session_absolute_hours: int = Field(default=12, ge=1, le=168)
    admin_login_attempts: int = Field(default=5, ge=2, le=50)
    admin_login_window_minutes: int = Field(default=15, ge=1, le=1440)

    database_url: str = ""
    redis_url: str = ""
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_worker_concurrency: int = Field(default=4, ge=1, le=64)
    run_event_stream_ttl_hours: int = Field(default=24, ge=1, le=720)

    node_storage_root: str = "/data/workspaces"
    node_quarantine_root: str = "/data/quarantine"
    neo4j_uri: str = ""
    neo4j_username: str = ""
    neo4j_password: str = ""
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    youtube_candidate_cap: int = Field(default=40, ge=5, le=100)
    comments_fetch_limit: int = Field(default=30, ge=0, le=100)
    comments_retain_limit: int = Field(default=20, ge=0, le=100)
    default_video_count: int = Field(default=5, ge=3, le=8)
    min_video_count: int = Field(default=3, ge=1, le=8)
    max_video_count: int = Field(default=8, ge=3, le=12)
    public_runs_per_hour: int = Field(default=3, ge=0)
    public_runs_per_day: int = Field(default=10, ge=0)
    public_concurrent_runs: int = Field(default=2, ge=0)
    public_run_cost_cap_usd: float = Field(default=3, ge=0)
    public_daily_cost_cap_usd: float = Field(default=25, ge=0)
    public_analysis_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.backend_cors_origins.split(",") if item.strip()]

    @property
    def is_local_development(self) -> bool:
        return self.app_env.lower() in {"development", "local", "test"}

    @property
    def node_roots(self) -> tuple[Path, Path]:
        return Path(self.node_storage_root), Path(self.node_quarantine_root)

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

    def v2_configuration_errors(self, role: ProcessRole = "api") -> list[str]:
        required = {
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "SESSION_SECRET": self.session_secret,
            "PUBLIC_TOKEN_HASH_SECRET": self.public_token_hash_secret,
            "RATE_LIMIT_HASH_SECRET": self.rate_limit_hash_secret,
        }
        if role in {"worker", "scheduler"}:
            required.update(
                {
                    "CELERY_BROKER_URL": self.celery_broker_url,
                    "YOUTUBE_API_KEY": self.youtube_api_key,
                    "OPENROUTER_API_KEY": self.openrouter_api_key,
                }
            )
        if role == "migrate":
            required.update(
                {
                    "ADMIN_EMAIL": self.admin_email,
                    "ADMIN_PASSWORD_HASH": self.admin_password_hash,
                }
            )

        errors = [f"{name} is required" for name, value in required.items() if not value]
        if self.admin_password:
            errors.append("ADMIN_PASSWORD is forbidden; use ADMIN_PASSWORD_HASH")
        if not self.cors_origins:
            errors.append("BACKEND_CORS_ORIGINS must contain at least one explicit origin")
        elif "*" in self.cors_origins:
            errors.append("BACKEND_CORS_ORIGINS cannot contain '*' when credentials are enabled")
        for name, value in (
            ("SESSION_SECRET", self.session_secret),
            ("PUBLIC_TOKEN_HASH_SECRET", self.public_token_hash_secret),
            ("RATE_LIMIT_HASH_SECRET", self.rate_limit_hash_secret),
        ):
            if value and len(value.encode("utf-8")) < 32:
                errors.append(f"{name} must contain at least 32 bytes")
        if self.admin_password_hash and not self.admin_password_hash.startswith("$argon2id$"):
            errors.append("ADMIN_PASSWORD_HASH must be an Argon2id hash")
        if self.min_video_count > self.default_video_count or self.default_video_count > self.max_video_count:
            errors.append("MIN_VIDEO_COUNT <= DEFAULT_VIDEO_COUNT <= MAX_VIDEO_COUNT is required")
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
