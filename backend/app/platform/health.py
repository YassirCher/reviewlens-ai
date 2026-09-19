from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from neo4j import GraphDatabase
from redis.exceptions import RedisError
from sqlalchemy import text

from app.cache import get_redis
from app.config import ProcessRole, Settings, settings
from app.db.session import get_engine


@dataclass(frozen=True)
class DependencyStatus:
    status: str
    latency_ms: int | None = None
    detail: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "ok"

    def public_dict(self) -> dict[str, str | int | bool | None]:
        return {"status": self.status, "available": self.available}

    def admin_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "status": self.status,
            "available": self.available,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HealthReport:
    status: str
    dependencies: dict[str, DependencyStatus]
    configuration: dict[str, bool]
    llmops: dict | None = None
    knowledge: dict | None = None
    research: dict | None = None

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "degraded"}

    def to_dict(self, *, detailed: bool) -> dict:
        serializer = DependencyStatus.admin_dict if detailed else DependencyStatus.public_dict
        payload = {
            "status": self.status,
            "ready": self.ready,
            "dependencies": {name: serializer(value) for name, value in self.dependencies.items()},
        }
        if detailed:
            payload["configuration"] = self.configuration
            payload["llmops"] = self.llmops or {}
            payload["knowledge"] = self.knowledge or {}
            payload["research"] = self.research or {}
        return payload


def _timed_probe(probe: Callable[[], str | None]) -> DependencyStatus:
    started = time.perf_counter()
    try:
        detail = probe()
        return DependencyStatus("ok", round((time.perf_counter() - started) * 1000), detail)
    except Exception as exc:
        return DependencyStatus(
            "unavailable",
            round((time.perf_counter() - started) * 1000),
            type(exc).__name__,
        )


def _postgres_probe() -> str | None:
    backend_root = Path(__file__).resolve().parents[2]
    alembic_config = AlembicConfig(str(backend_root / "alembic.ini"))
    script = ScriptDirectory.from_config(alembic_config)
    expected = set(script.get_heads())
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current != expected:
        raise RuntimeError("database migrations are not current")
    return "migrations_current"


def _redis_probe() -> str | None:
    if not get_redis().ping():
        raise RuntimeError("Redis PING failed")
    return None


def _storage_probe(config: Settings) -> str | None:
    for root in config.node_roots:
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"storage root unavailable: {root.name}")
        if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError(f"storage root is not writable: {root.name}")
    return "workspace_and_quarantine_ready"


def _neo4j_probe(config: Settings) -> str | None:
    if not config.neo4j_uri or not config.neo4j_username or not config.neo4j_password:
        raise RuntimeError("Neo4j configuration incomplete")
    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_username, config.neo4j_password),
        connection_timeout=2,
    )
    try:
        driver.verify_connectivity()
    finally:
        driver.close()
    return None


def _qdrant_probe(config: Settings) -> str | None:
    if not config.qdrant_url:
        raise RuntimeError("Qdrant configuration missing")
    headers = {"api-key": config.qdrant_api_key} if config.qdrant_api_key else None
    response = httpx.get(f"{config.qdrant_url.rstrip('/')}/readyz", headers=headers, timeout=2)
    response.raise_for_status()
    return None


def collect_health(config: Settings = settings) -> HealthReport:
    config_errors = config.v2_configuration_errors("api")
    dependencies = {
        "postgres": _timed_probe(_postgres_probe),
        "redis": _timed_probe(_redis_probe),
        "markdown_storage": _timed_probe(lambda: _storage_probe(config)),
        "neo4j": _timed_probe(lambda: _neo4j_probe(config)),
        "qdrant": _timed_probe(lambda: _qdrant_probe(config)),
    }
    configuration = {
        "valid": not config_errors,
        "youtube_configured": bool(config.youtube_api_key),
        "openrouter_configured": bool(config.openrouter_api_key),
        "neo4j_configured": bool(config.neo4j_uri and config.neo4j_username and config.neo4j_password),
        "qdrant_configured": bool(config.qdrant_url),
    }
    critical = ("postgres", "redis", "markdown_storage")
    if config_errors or any(not dependencies[name].available for name in critical):
        status = "not_ready"
    elif any(not dependencies[name].available for name in ("neo4j", "qdrant")):
        status = "degraded"
    else:
        status = "ready"
    llmops: dict = {}
    knowledge: dict = {}
    research: dict = {}
    # These detailed probes also query PostgreSQL. When the primary probe has
    # failed, repeating several connection timeouts can make /health/ready
    # itself unreachable instead of promptly returning 503.
    if dependencies["postgres"].available:
        try:
            from app.llmops.operations import llmops_health

            llmops = llmops_health(config)
        except Exception as exc:
            llmops = {"status": "unavailable", "detail": type(exc).__name__}
        try:
            from app.knowledge.health import knowledge_health

            knowledge = knowledge_health(config)
        except Exception as exc:
            knowledge = {"status": "unavailable", "detail": type(exc).__name__}
        try:
            from app.tools.health import research_health

            research = research_health(config)
        except Exception as exc:
            research = {"status": "unavailable", "detail": type(exc).__name__}
    return HealthReport(
        status=status,
        dependencies=dependencies,
        configuration=configuration,
        llmops=llmops,
        knowledge=knowledge,
        research=research,
    )


def validate_process(role: ProcessRole, config: Settings = settings) -> list[str]:
    errors = config.v2_configuration_errors(role)
    if role in {"worker", "scheduler"}:
        for root in config.node_roots:
            if not root.exists() or not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
                errors.append(f"{root.name} storage root must exist and be writable")
    return errors


def worker_is_reachable() -> bool:
    from app.worker import celery_app

    replies = celery_app.control.ping(timeout=2)
    return bool(replies)


def process_dependencies_are_ready(role: ProcessRole, config: Settings = settings) -> bool:
    """Check the process's own configuration, database, Redis, and storage."""
    if validate_process(role, config):
        return False
    return all(
        status.available
        for status in (
            _timed_probe(_postgres_probe),
            _timed_probe(_redis_probe),
            _timed_probe(lambda: _storage_probe(config)),
        )
    )


def scheduler_heartbeat_is_fresh(max_age_seconds: int = 90) -> bool:
    try:
        value = get_redis().get("reviewlens:health:scheduler")
    except RedisError:
        return False
    if value is None:
        return False
    try:
        return time.time() - float(value) <= max_age_seconds
    except ValueError:
        return False
