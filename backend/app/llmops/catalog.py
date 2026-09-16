from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import get_redis
from app.config import Settings, settings
from app.db.models import (
    OpenRouterCatalogRefresh,
    OpenRouterEndpointSnapshot,
    OpenRouterModelSnapshot,
    OpenRouterProviderSnapshot,
)
from app.db.session import session_scope
from app.llmops.client import OpenRouterClient
from app.llmops.contracts import OpenRouterError
from app.runtime.contracts import canonical_json_hash

CatalogKind = Literal["chat_models", "embedding_models", "providers", "model_endpoints"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any, fallback: Any) -> Any:
    return value if isinstance(value, type(fallback)) else fallback


def _provider_slug(payload: dict[str, Any]) -> str:
    explicit = payload.get("provider_slug") or payload.get("provider") or payload.get("slug")
    if explicit:
        return str(explicit).strip().lower()
    tag = str(payload.get("tag") or "")
    if "/" in tag:
        return tag.split("/", 1)[0].strip().lower()
    return "unknown"


def _normalize_model(payload: dict[str, Any], kind: str, refresh_id: uuid.UUID, fetched_at: datetime) -> OpenRouterModelSnapshot:
    slug = str(payload.get("id") or payload.get("canonical_slug") or "").strip()
    if not slug or "/" not in slug:
        raise ValueError("catalog model is missing a canonical slug")
    architecture = _json_value(payload.get("architecture"), {})
    top_provider = _json_value(payload.get("top_provider"), {})
    created = payload.get("created")
    return OpenRouterModelSnapshot(
        id=uuid.uuid4(),
        refresh_id=refresh_id,
        model_kind=kind,
        slug=slug,
        canonical_slug=str(payload.get("canonical_slug") or slug),
        author=slug.split("/", 1)[0],
        name=str(payload.get("name") or slug),
        description=str(payload.get("description") or ""),
        created_timestamp=int(created) if isinstance(created, (int, float)) else None,
        expiration_date=str(payload.get("expiration_date")) if payload.get("expiration_date") else None,
        context_length=int(payload["context_length"]) if isinstance(payload.get("context_length"), (int, float)) else None,
        max_completion_tokens=(
            int(top_provider["max_completion_tokens"])
            if isinstance(top_provider.get("max_completion_tokens"), (int, float))
            else None
        ),
        input_modalities=list(_json_value(architecture.get("input_modalities"), [])),
        output_modalities=list(_json_value(architecture.get("output_modalities"), [])),
        architecture=architecture,
        supported_parameters=list(_json_value(payload.get("supported_parameters"), [])),
        pricing=_json_value(payload.get("pricing"), {}),
        top_provider=top_provider,
        raw_payload_hash=canonical_json_hash(payload),
        fetched_at=fetched_at,
    )


def _normalize_provider(payload: dict[str, Any], refresh_id: uuid.UUID, fetched_at: datetime) -> OpenRouterProviderSnapshot:
    slug = _provider_slug(payload)
    if slug == "unknown":
        raise ValueError("provider catalog item is missing a slug")
    return OpenRouterProviderSnapshot(
        id=uuid.uuid4(),
        refresh_id=refresh_id,
        slug=slug,
        name=str(payload.get("name") or payload.get("display_name") or slug),
        privacy=_json_value(payload.get("privacy"), {}),
        status=str(payload.get("status")) if payload.get("status") else None,
        metadata_json={
            key: value
            for key, value in payload.items()
            if key not in {"slug", "id", "name", "display_name", "privacy", "status"}
        },
        raw_payload_hash=canonical_json_hash(payload),
        fetched_at=fetched_at,
    )


def _normalize_endpoint(payload: dict[str, Any], model_slug: str, refresh_id: uuid.UUID, fetched_at: datetime) -> OpenRouterEndpointSnapshot:
    provider_slug = _provider_slug(payload)
    endpoint_key = str(payload.get("id") or payload.get("tag") or canonical_json_hash(payload))[:400]
    return OpenRouterEndpointSnapshot(
        id=uuid.uuid4(),
        refresh_id=refresh_id,
        model_slug=model_slug,
        endpoint_key=endpoint_key,
        provider_slug=provider_slug,
        provider_name=str(payload.get("provider_name") or payload.get("name") or provider_slug),
        context_length=int(payload["context_length"]) if isinstance(payload.get("context_length"), (int, float)) else None,
        max_completion_tokens=(
            int(payload["max_completion_tokens"])
            if isinstance(payload.get("max_completion_tokens"), (int, float))
            else None
        ),
        quantization=str(payload.get("quantization")) if payload.get("quantization") else None,
        supported_parameters=list(_json_value(payload.get("supported_parameters"), [])),
        pricing=_json_value(payload.get("pricing"), {}),
        performance={
            key: payload[key]
            for key in ("latency_last_30m", "throughput_last_30m")
            if key in payload
        },
        moderation=_json_value(payload.get("moderation"), {}),
        privacy=_json_value(payload.get("privacy"), {}),
        status=str(payload.get("status")) if payload.get("status") else None,
        raw_payload_hash=canonical_json_hash(payload),
        fetched_at=fetched_at,
    )


def _begin_refresh(kind: CatalogKind, target_slug: str | None = None) -> uuid.UUID:
    refresh_id = uuid.uuid4()
    with session_scope() as db:
        db.add(
            OpenRouterCatalogRefresh(
                id=refresh_id,
                catalog_kind=kind,
                target_slug=target_slug,
                status="running",
                started_at=utc_now(),
            )
        )
    return refresh_id


def _fail_refresh(refresh_id: uuid.UUID, exc: Exception) -> None:
    with session_scope() as db:
        refresh = db.get(OpenRouterCatalogRefresh, refresh_id)
        if refresh and refresh.status == "running":
            refresh.status = "failed"
            refresh.completed_at = utc_now()
            refresh.error_category = exc.category.value if isinstance(exc, OpenRouterError) else "catalog_invalid"
            error_code = (
                exc.provider_code or exc.category.value
                if isinstance(exc, OpenRouterError)
                else type(exc).__name__
            )
            refresh.error_code = error_code[:120]


def _cache_key(kind: CatalogKind, target_slug: str | None = None) -> str:
    suffix = f":{target_slug}" if target_slug else ""
    return f"reviewlens:openrouter:catalog:{kind}{suffix}"


def _cache_rows(redis_client: Redis | None, kind: CatalogKind, rows: list[dict[str, Any]], config: Settings, target_slug: str | None = None) -> None:
    if redis_client is None:
        return
    redis_client.setex(
        _cache_key(kind, target_slug),
        config.openrouter_catalog_stale_minutes * 60,
        json.dumps(rows, separators=(",", ":"), ensure_ascii=True, default=str),
    )


async def refresh_model_catalog(
    model_kind: Literal["chat", "embedding"],
    *,
    client: OpenRouterClient | None = None,
    redis_client: Redis | None = None,
    config: Settings = settings,
) -> dict[str, Any]:
    kind: CatalogKind = "chat_models" if model_kind == "chat" else "embedding_models"
    refresh_id = _begin_refresh(kind)
    gateway = client or OpenRouterClient(config)
    try:
        payloads = await gateway.list_models(model_kind)
        fetched_at = utc_now()
        rows = [_normalize_model(item, model_kind, refresh_id, fetched_at) for item in payloads]
        with session_scope() as db:
            refresh = db.get(OpenRouterCatalogRefresh, refresh_id)
            assert refresh is not None
            db.add_all(rows)
            refresh.status = "succeeded"
            refresh.payload_hash = canonical_json_hash(payloads)
            refresh.item_count = len(rows)
            refresh.completed_at = fetched_at
        cache_payload = [_model_dict(item, available=True) for item in rows]
        _cache_rows(redis_client, kind, cache_payload, config)
        return {"refresh_id": str(refresh_id), "kind": kind, "item_count": len(rows)}
    except Exception as exc:
        _fail_refresh(refresh_id, exc)
        raise


async def refresh_provider_catalog(
    *,
    client: OpenRouterClient | None = None,
    redis_client: Redis | None = None,
    config: Settings = settings,
) -> dict[str, Any]:
    refresh_id = _begin_refresh("providers")
    gateway = client or OpenRouterClient(config)
    try:
        payloads = await gateway.list_providers()
        fetched_at = utc_now()
        rows = [_normalize_provider(item, refresh_id, fetched_at) for item in payloads]
        with session_scope() as db:
            refresh = db.get(OpenRouterCatalogRefresh, refresh_id)
            assert refresh is not None
            db.add_all(rows)
            refresh.status = "succeeded"
            refresh.payload_hash = canonical_json_hash(payloads)
            refresh.item_count = len(rows)
            refresh.completed_at = fetched_at
        _cache_rows(redis_client, "providers", [_provider_dict(item) for item in rows], config)
        return {"refresh_id": str(refresh_id), "kind": "providers", "item_count": len(rows)}
    except Exception as exc:
        _fail_refresh(refresh_id, exc)
        raise


async def refresh_model_endpoints(
    model_slug: str,
    *,
    client: OpenRouterClient | None = None,
    redis_client: Redis | None = None,
    config: Settings = settings,
) -> dict[str, Any]:
    refresh_id = _begin_refresh("model_endpoints", model_slug)
    gateway = client or OpenRouterClient(config)
    try:
        payloads = await gateway.list_endpoints(model_slug)
        fetched_at = utc_now()
        rows = [_normalize_endpoint(item, model_slug, refresh_id, fetched_at) for item in payloads]
        with session_scope() as db:
            refresh = db.get(OpenRouterCatalogRefresh, refresh_id)
            assert refresh is not None
            db.add_all(rows)
            refresh.status = "succeeded"
            refresh.payload_hash = canonical_json_hash(payloads)
            refresh.item_count = len(rows)
            refresh.completed_at = fetched_at
        _cache_rows(
            redis_client,
            "model_endpoints",
            [_endpoint_dict(item) for item in rows],
            config,
            target_slug=model_slug,
        )
        return {"refresh_id": str(refresh_id), "kind": "model_endpoints", "item_count": len(rows)}
    except Exception as exc:
        _fail_refresh(refresh_id, exc)
        raise


async def refresh_catalogs(
    *,
    client: OpenRouterClient | None = None,
    redis_client: Redis | None = None,
    manual: bool = False,
    config: Settings = settings,
) -> dict[str, Any]:
    cache = redis_client or get_redis()
    if manual:
        acquired = cache.set(
            "reviewlens:openrouter:manual-refresh",
            "1",
            nx=True,
            ex=config.openrouter_manual_refresh_cooldown_seconds,
        )
        if not acquired:
            raise RuntimeError("openrouter_catalog_refresh_throttled")
    gateway = client or OpenRouterClient(config)
    results = [
        await refresh_model_catalog("chat", client=gateway, redis_client=cache, config=config),
        await refresh_model_catalog("embedding", client=gateway, redis_client=cache, config=config),
        await refresh_provider_catalog(client=gateway, redis_client=cache, config=config),
    ]
    for model_slug in config.v2_agent_model_slugs:
        results.append(
            await refresh_model_endpoints(
                model_slug,
                client=gateway,
                redis_client=cache,
                config=config,
            )
        )
    return {"status": "succeeded", "refreshes": results}


def _latest_refresh(db: Session, kind: CatalogKind, target_slug: str | None = None) -> OpenRouterCatalogRefresh | None:
    statement = (
        select(OpenRouterCatalogRefresh)
        .where(
            OpenRouterCatalogRefresh.catalog_kind == kind,
            OpenRouterCatalogRefresh.status == "succeeded",
        )
        .order_by(OpenRouterCatalogRefresh.completed_at.desc())
        .limit(1)
    )
    if target_slug is not None:
        statement = statement.where(OpenRouterCatalogRefresh.target_slug == target_slug)
    return db.scalar(statement)


def _latest_attempt(db: Session, kind: CatalogKind, target_slug: str | None = None) -> OpenRouterCatalogRefresh | None:
    statement = (
        select(OpenRouterCatalogRefresh)
        .where(OpenRouterCatalogRefresh.catalog_kind == kind)
        .order_by(OpenRouterCatalogRefresh.started_at.desc())
        .limit(1)
    )
    if target_slug is not None:
        statement = statement.where(OpenRouterCatalogRefresh.target_slug == target_slug)
    return db.scalar(statement)


def _stale(refresh: OpenRouterCatalogRefresh, config: Settings, now: datetime | None = None) -> bool:
    completed = refresh.completed_at or refresh.started_at
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return (now or utc_now()) - completed > timedelta(minutes=config.openrouter_catalog_stale_minutes)


def _status_metadata(
    db: Session,
    kind: CatalogKind,
    refresh: OpenRouterCatalogRefresh,
    config: Settings,
    target_slug: str | None = None,
) -> tuple[bool, OpenRouterCatalogRefresh | None]:
    attempt = _latest_attempt(db, kind, target_slug)
    failed_after_success = bool(
        attempt
        and attempt.status == "failed"
        and attempt.started_at >= refresh.started_at
        and attempt.id != refresh.id
    )
    return _stale(refresh, config) or failed_after_success, attempt


def _prompt_price(pricing: dict[str, Any]) -> Decimal | None:
    try:
        value = Decimal(str(pricing["prompt"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def _model_dict(row: OpenRouterModelSnapshot, *, available: bool) -> dict[str, Any]:
    return {
        "slug": row.slug,
        "canonical_slug": row.canonical_slug,
        "author": row.author,
        "name": row.name,
        "description": row.description,
        "model_kind": row.model_kind,
        "context_length": row.context_length,
        "max_completion_tokens": row.max_completion_tokens,
        "input_modalities": row.input_modalities,
        "output_modalities": row.output_modalities,
        "supported_parameters": row.supported_parameters,
        "pricing": row.pricing,
        "top_provider": row.top_provider,
        "available": available,
        "fetched_at": row.fetched_at.isoformat(),
    }


def _provider_dict(row: OpenRouterProviderSnapshot) -> dict[str, Any]:
    return {"slug": row.slug, "name": row.name, "privacy": row.privacy, "status": row.status}


def _endpoint_dict(row: OpenRouterEndpointSnapshot) -> dict[str, Any]:
    return {
        "model_slug": row.model_slug,
        "provider_slug": row.provider_slug,
        "provider_name": row.provider_name,
        "context_length": row.context_length,
        "max_completion_tokens": row.max_completion_tokens,
        "quantization": row.quantization,
        "supported_parameters": row.supported_parameters,
        "pricing": row.pricing,
        "performance": row.performance,
        "privacy": row.privacy,
        "status": row.status,
    }


def search_models(
    db: Session,
    *,
    model_kind: Literal["chat", "embedding"],
    query: str | None = None,
    author: str | None = None,
    capability: str | None = None,
    minimum_context: int | None = None,
    maximum_prompt_price: Decimal | None = None,
    provider: str | None = None,
    available_only: bool = True,
    sort: Literal["name", "context", "prompt_price"] = "name",
    acknowledge_stale: bool = False,
    config: Settings = settings,
) -> dict[str, Any]:
    kind: CatalogKind = "chat_models" if model_kind == "chat" else "embedding_models"
    refresh = _latest_refresh(db, kind)
    if refresh is None:
        return {"status": "missing", "stale": True, "fetched_at": None, "models": []}
    is_stale, latest_attempt = _status_metadata(db, kind, refresh, config)
    if is_stale and not acknowledge_stale:
        status = "stale"
    else:
        status = "ok"
    rows = list(
        db.scalars(
            select(OpenRouterModelSnapshot).where(
                OpenRouterModelSnapshot.refresh_id == refresh.id,
                OpenRouterModelSnapshot.model_kind == model_kind,
            )
        )
    )
    needle = query.casefold().strip() if query else None
    filtered = []
    for row in rows:
        if needle and needle not in f"{row.slug} {row.name} {row.description}".casefold():
            continue
        if author and row.author != author:
            continue
        if capability and capability not in row.supported_parameters:
            continue
        if minimum_context is not None and (row.context_length or 0) < minimum_context:
            continue
        prompt_price = _prompt_price(row.pricing)
        if maximum_prompt_price is not None and (
            prompt_price is None or prompt_price > maximum_prompt_price
        ):
            continue
        if provider:
            endpoints = current_endpoints(db, row.slug, config)
            if not any(item["provider_slug"] == provider for item in endpoints["endpoints"]):
                continue
        filtered.append(_model_dict(row, available=True))
    if not available_only:
        historical = list(
            db.scalars(
                select(OpenRouterModelSnapshot)
                .where(OpenRouterModelSnapshot.model_kind == model_kind)
                .order_by(OpenRouterModelSnapshot.fetched_at.desc())
            )
        )
        current_slugs = {item["slug"] for item in filtered}
        for row in historical:
            if row.slug not in current_slugs:
                filtered.append(_model_dict(row, available=False))
                current_slugs.add(row.slug)
    if sort == "context":
        filtered.sort(key=lambda item: (-(item["context_length"] or 0), item["slug"]))
    elif sort == "prompt_price":
        filtered.sort(
            key=lambda item: (
                _prompt_price(item["pricing"]) is None,
                _prompt_price(item["pricing"]) or Decimal(0),
                item["slug"],
            )
        )
    else:
        filtered.sort(key=lambda item: (item["name"].casefold(), item["slug"]))
    return {
        "status": status,
        "stale": is_stale,
        "fetched_at": (refresh.completed_at or refresh.started_at).isoformat(),
        "last_attempt_at": latest_attempt.started_at.isoformat() if latest_attempt else None,
        "last_error_category": (
            latest_attempt.error_category if latest_attempt and latest_attempt.status == "failed" else None
        ),
        "models": filtered,
    }


def current_endpoints(db: Session, model_slug: str, config: Settings = settings) -> dict[str, Any]:
    refresh = _latest_refresh(db, "model_endpoints", model_slug)
    if refresh is None:
        return {"status": "missing", "stale": True, "fetched_at": None, "endpoints": []}
    rows = list(
        db.scalars(
            select(OpenRouterEndpointSnapshot).where(OpenRouterEndpointSnapshot.refresh_id == refresh.id)
        )
    )
    is_stale, latest_attempt = _status_metadata(
        db,
        "model_endpoints",
        refresh,
        config,
        model_slug,
    )
    return {
        "status": "stale" if is_stale else "ok",
        "stale": is_stale,
        "fetched_at": (refresh.completed_at or refresh.started_at).isoformat(),
        "last_attempt_at": latest_attempt.started_at.isoformat() if latest_attempt else None,
        "last_error_category": (
            latest_attempt.error_category if latest_attempt and latest_attempt.status == "failed" else None
        ),
        "endpoints": [_endpoint_dict(row) for row in rows],
    }
