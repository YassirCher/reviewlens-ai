from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProcessRole = Literal["api", "worker", "scheduler", "migrate"]


class Settings(BaseSettings):
    youtube_api_key: str = ""
    youtube_region_code: str = "US"
    youtube_relevance_language: str = "en"

    openrouter_api_key: str = ""
    openrouter_app_url: str = "http://localhost:3000"
    openrouter_app_name: str = "ReviewLens"
    openrouter_management_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_catalog_refresh_minutes: int = Field(default=15, ge=1, le=1440)
    openrouter_request_timeout_seconds: int = Field(default=300, ge=1, le=600)
    openrouter_connect_timeout_seconds: float = Field(default=10, gt=0, le=60)
    openrouter_write_timeout_seconds: float = Field(default=30, gt=0, le=120)
    openrouter_pool_timeout_seconds: float = Field(default=10, gt=0, le=60)
    openrouter_max_attempts: int = Field(default=3, ge=1, le=5)
    openrouter_retry_base_seconds: float = Field(default=1, ge=0, le=30)
    openrouter_retry_max_seconds: float = Field(default=15, ge=0, le=120)
    openrouter_catalog_stale_minutes: int = Field(default=60, ge=15, le=10080)
    openrouter_manual_refresh_cooldown_seconds: int = Field(default=60, ge=10, le=3600)
    openrouter_reconciliation_interval_seconds: int = Field(default=60, ge=10, le=3600)
    openrouter_reconciliation_max_age_minutes: int = Field(default=60, ge=15, le=10080)
    openrouter_reconciliation_batch_size: int = Field(default=50, ge=1, le=500)
    openrouter_credit_refresh_minutes: int = Field(default=5, ge=1, le=1440)
    openrouter_live_smoke_enabled: bool = False
    openrouter_smoke_chat_model: str = ""
    openrouter_smoke_embedding_model: str = ""
    openrouter_smoke_max_cost_microusd: int = Field(default=10_000, ge=1, le=100_000)

    # Initial seed model. Published policies and active pointers own routing.
    v2_agent_chat_models: str = "deepseek/deepseek-v4-flash"
    v2_agent_max_concurrency: int = Field(default=4, ge=1, le=32)
    v2_analysis_run_timeout_seconds: int = Field(default=1800, ge=60, le=3600)

    app_env: str = "development"
    app_public_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    backend_cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    v2_max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    operations_error_rate_min_runs: int = Field(default=5, ge=1, le=10_000)
    raw_content_encryption_key: str = Field(default="", exclude=True, repr=False)

    admin_email: str = ""
    admin_password_hash: str = ""
    # Declared solely so a legacy/plaintext environment value can be rejected
    # explicitly instead of being silently ignored by BaseSettings.
    admin_password: str = Field(default="", exclude=True, repr=False)
    admin_session_cookie: str = "reviewlens_admin_session"
    session_secret: str = ""
    public_token_hash_secret: str = ""
    rate_limit_hash_secret: str = ""
    anonymous_session_cookie: str = "reviewlens_anonymous_session"
    anonymous_session_idle_hours: int = Field(default=24, ge=1, le=168)
    anonymous_session_absolute_days: int = Field(default=7, ge=1, le=90)
    public_queue_capacity: int = Field(default=20, ge=0, le=10000)
    session_idle_minutes: int = Field(default=60, ge=5, le=1440)
    session_absolute_hours: int = Field(default=12, ge=1, le=168)
    admin_login_attempts: int = Field(default=5, ge=2, le=50)
    admin_login_window_minutes: int = Field(default=15, ge=1, le=1440)

    database_url: str = ""
    redis_url: str = ""
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_worker_concurrency: int = Field(default=4, ge=1, le=64)
    celery_visibility_timeout_seconds: int = Field(default=2400, ge=60, le=86400)
    run_event_stream_ttl_hours: int = Field(default=24, ge=1, le=720)
    run_event_stream_max_length: int = Field(default=2000, ge=100, le=100000)
    runtime_outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    runtime_outbox_lease_seconds: int = Field(default=60, ge=10, le=900)
    runtime_task_lease_seconds: int = Field(default=480, ge=30, le=1800)
    runtime_recovery_interval_seconds: int = Field(default=30, ge=5, le=600)
    runtime_dispatch_lock_seconds: int = Field(default=30, ge=5, le=300)

    node_storage_root: str = "/data/workspaces"
    node_quarantine_root: str = "/data/quarantine"
    neo4j_uri: str = ""
    neo4j_username: str = ""
    neo4j_password: str = ""
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    context_reconciliation_interval_seconds: int = Field(default=300, ge=30, le=86400)
    projection_outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    projection_backlog_alert_threshold: int = Field(default=100, ge=1, le=100000)
    context_node_preview_characters: int = Field(default=512, ge=0, le=4000)

    youtube_candidate_cap: int = Field(default=40, ge=5, le=100)
    comments_fetch_limit: int = Field(default=30, ge=0, le=30)
    comments_retain_limit: int = Field(default=20, ge=0, le=20)
    youtube_base_url: str = "https://www.googleapis.com/youtube/v3"
    youtube_request_timeout_seconds: float = Field(default=20, gt=0, le=120)
    youtube_transcript_timeout_seconds: float = Field(default=45, gt=0, le=180)
    youtube_network_max_attempts: int = Field(default=3, ge=1, le=3)
    youtube_retry_base_seconds: float = Field(default=1, ge=0, le=30)
    youtube_retry_max_seconds: float = Field(default=8, ge=0, le=60)
    youtube_search_query_limit: int = Field(default=4, ge=1, le=4)
    youtube_search_daily_call_limit: int = Field(default=100, ge=0, le=100000)
    youtube_data_daily_unit_limit: int = Field(default=10000, ge=0, le=10000000)
    youtube_min_review_duration_seconds: int = Field(default=180, ge=30, le=3600)
    youtube_min_relevance_score: float = Field(default=0.5, ge=0, le=1)
    youtube_transcript_chunk_target_characters: int = Field(default=6000, ge=500, le=50000)
    youtube_transcript_chunk_max_characters: int = Field(default=8000, ge=500, le=100000)
    youtube_tool_max_output_bytes: int = Field(default=1000000, ge=1000, le=10000000)
    youtube_live_smoke_enabled: bool = False
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
    def v2_agent_model_slugs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(item.strip() for item in self.v2_agent_chat_models.split(",") if item.strip())
        )

    @property
    def node_roots(self) -> tuple[Path, Path]:
        return Path(self.node_storage_root), Path(self.node_quarantine_root)

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
        if self.openrouter_retry_max_seconds < self.openrouter_retry_base_seconds:
            errors.append(
                "OPENROUTER_RETRY_MAX_SECONDS must be greater than or equal to "
                "OPENROUTER_RETRY_BASE_SECONDS"
            )
        if not self.v2_agent_model_slugs:
            errors.append("V2_AGENT_CHAT_MODELS must contain at least one model slug")
        elif len(self.v2_agent_model_slugs) != 1:
            errors.append("V2_AGENT_CHAT_MODELS must contain exactly one model slug in Phase 6")
        elif any("/" not in item for item in self.v2_agent_model_slugs):
            errors.append("V2_AGENT_CHAT_MODELS must contain canonical author/model slugs")
        if self.youtube_retry_max_seconds < self.youtube_retry_base_seconds:
            errors.append(
                "YOUTUBE_RETRY_MAX_SECONDS must be greater than or equal to "
                "YOUTUBE_RETRY_BASE_SECONDS"
            )
        if self.youtube_transcript_chunk_target_characters > self.youtube_transcript_chunk_max_characters:
            errors.append(
                "YOUTUBE_TRANSCRIPT_CHUNK_TARGET_CHARACTERS must not exceed "
                "YOUTUBE_TRANSCRIPT_CHUNK_MAX_CHARACTERS"
            )
        if (
            self.youtube_base_url.rstrip("/") != "https://www.googleapis.com/youtube/v3"
            and self.app_env.lower() != "test"
        ):
            errors.append("YOUTUBE_BASE_URL may only be overridden when APP_ENV=test")
        if (
            self.openrouter_base_url
            and not self.is_local_development
            and not self.openrouter_base_url.startswith("https://")
        ):
            errors.append("OPENROUTER_BASE_URL must use HTTPS outside local development and tests")
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
