from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.config import Settings, settings
from app.db.models import OpenRouterAccountState, OpenRouterCatalogRefresh, UsageEvent
from app.db.session import session_scope
from app.llmops.accounting import (
    finalize_successful_request,
    finalize_unreconcilable_request,
    update_account_state,
)
from app.llmops.client import OpenRouterClient, normalize_usage
from app.llmops.contracts import OpenRouterError, dollars_to_microusd
from app.services.audit_service import add_audit_event


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def refresh_credit_state(
    *, client: OpenRouterClient | None = None, config: Settings = settings
) -> dict[str, Any]:
    if not config.openrouter_management_key:
        return {"status": "not_configured"}
    gateway = client or OpenRouterClient(config)
    try:
        payload = await gateway.credits()
    except OpenRouterError as exc:
        update_account_state(category=exc.category, error_code=exc.provider_code, config=config)
        return {"status": "failed", "error_category": exc.category.value}
    credits = dollars_to_microusd(payload.get("total_credits") or payload.get("credits"))
    usage = dollars_to_microusd(payload.get("total_usage") or payload.get("usage"))
    update_account_state(credits_microusd=credits, usage_microusd=usage, config=config)
    return {"status": "succeeded", "credits_available": credits is not None}


def _claim_pending(config: Settings) -> list[str]:
    now = utc_now()
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(UsageEvent)
                .where(
                    UsageEvent.usage_status == "pending",
                    UsageEvent.generation_id.is_not(None),
                    UsageEvent.next_reconciliation_at.is_not(None),
                    UsageEvent.next_reconciliation_at <= now,
                )
                .order_by(UsageEvent.next_reconciliation_at)
                .with_for_update(skip_locked=True)
                .limit(config.openrouter_reconciliation_batch_size)
            )
        )
        for row in rows:
            row.reconciliation_attempts += 1
            delay = min(900, 2 ** min(row.reconciliation_attempts, 9))
            row.next_reconciliation_at = now + timedelta(seconds=delay)
        return [str(row.id) for row in rows]


def _generation_usage(payload: dict[str, Any]):
    nested = payload.get("usage")
    if isinstance(nested, dict):
        return normalize_usage(nested)
    flat = {
        "prompt_tokens": payload.get("tokens_prompt") or payload.get("prompt_tokens"),
        "completion_tokens": payload.get("tokens_completion") or payload.get("completion_tokens"),
        "total_tokens": payload.get("tokens") or payload.get("total_tokens"),
        "cost": payload.get("total_cost") or payload.get("cost"),
        "completion_tokens_details": {
            "reasoning_tokens": payload.get("reasoning_tokens") or 0,
        },
        "prompt_tokens_details": {
            "cached_tokens": payload.get("cached_tokens") or 0,
            "cache_write_tokens": payload.get("cache_write_tokens") or 0,
        },
    }
    return normalize_usage(flat)


def _mark_reconciliation_failure(event_id: str, config: Settings) -> None:
    now = utc_now()
    with session_scope() as db:
        event = db.scalar(select(UsageEvent).where(UsageEvent.id == event_id).with_for_update())
        if event is None or event.usage_status != "pending":
            return
        created = event.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created >= timedelta(minutes=config.openrouter_reconciliation_max_age_minutes):
            reservation_id = event.reservation_id
        else:
            return
    finalize_unreconcilable_request(reservation_id)


def _finalize_overdue_without_generation(config: Settings) -> int:
    cutoff = utc_now() - timedelta(minutes=config.openrouter_reconciliation_max_age_minutes)
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(UsageEvent)
                .where(
                    UsageEvent.usage_status == "pending",
                    UsageEvent.generation_id.is_(None),
                    UsageEvent.created_at < cutoff,
                )
                .order_by(UsageEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(config.openrouter_reconciliation_batch_size)
            )
        )
        reservation_ids = [row.reservation_id for row in rows]
    for reservation_id in reservation_ids:
        finalize_unreconcilable_request(reservation_id)
        with session_scope() as db:
            event = db.scalar(select(UsageEvent).where(UsageEvent.reservation_id == reservation_id))
            if event:
                add_audit_event(
                    db,
                    action="llmops.usage_unreconcilable",
                    actor_type="scheduler",
                    target_type="usage_event",
                    target_id=str(event.id),
                    request_id=uuid.UUID(event.app_request_id),
                    safe_metadata={"usage_status": "unreconcilable"},
                )
    return len(reservation_ids)


async def reconcile_pending_usage(
    *, client: OpenRouterClient | None = None, config: Settings = settings
) -> dict[str, int]:
    gateway = client or OpenRouterClient(config)
    unreconcilable = _finalize_overdue_without_generation(config)
    claimed = _claim_pending(config)
    reconciled = 0
    failed = 0
    for event_id in claimed:
        with session_scope() as db:
            event = db.get(UsageEvent, uuid.UUID(event_id))
            generation_id = event.generation_id if event else None
        if not event or not generation_id:
            continue
        try:
            payload = await gateway.generation(generation_id)
            usage = _generation_usage(payload)
            if usage is None:
                raise RuntimeError("generation_usage_missing")
            finalize_successful_request(
                event.reservation_id,
                generation_id=generation_id,
                actual_model=str(payload.get("model") or event.actual_model or "") or None,
                actual_provider=str(payload.get("provider_name") or payload.get("provider") or event.actual_provider or "") or None,
                usage_value=usage,
                latency_ms=int(payload.get("latency") or event.latency_ms or 0),
                finish_reason=str(payload.get("finish_reason") or event.finish_reason or "") or None,
                service_tier=str(payload.get("service_tier") or event.service_tier or "") or None,
                reconciled=True,
                config=config,
            )
            with session_scope() as db:
                reconciled_event = db.get(UsageEvent, uuid.UUID(event_id))
                if reconciled_event:
                    add_audit_event(
                        db,
                        action="llmops.usage_reconciled",
                        actor_type="scheduler",
                        target_type="usage_event",
                        target_id=event_id,
                        request_id=uuid.UUID(reconciled_event.app_request_id),
                        safe_metadata={"usage_status": "reconciled"},
                    )
            reconciled += 1
        except Exception:
            failed += 1
            _mark_reconciliation_failure(event_id, config)
    return {
        "claimed": len(claimed),
        "reconciled": reconciled,
        "failed": failed,
        "unreconcilable": unreconcilable,
    }


def llmops_health(config: Settings = settings) -> dict[str, Any]:
    now = utc_now()
    required_kinds = ("chat_models", "embedding_models", "providers")
    with session_scope() as db:
        catalog_kinds: dict[str, dict[str, Any]] = {}
        for kind in required_kinds:
            success = db.scalar(
                select(OpenRouterCatalogRefresh)
                .where(
                    OpenRouterCatalogRefresh.catalog_kind == kind,
                    OpenRouterCatalogRefresh.status == "succeeded",
                )
                .order_by(OpenRouterCatalogRefresh.completed_at.desc())
                .limit(1)
            )
            attempt = db.scalar(
                select(OpenRouterCatalogRefresh)
                .where(OpenRouterCatalogRefresh.catalog_kind == kind)
                .order_by(OpenRouterCatalogRefresh.started_at.desc())
                .limit(1)
            )
            kind_status = "missing"
            if success:
                completed = success.completed_at or success.started_at
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                failed_after_success = bool(
                    attempt
                    and attempt.status == "failed"
                    and attempt.id != success.id
                    and attempt.started_at >= success.started_at
                )
                kind_status = (
                    "stale"
                    if failed_after_success
                    or now - completed > timedelta(minutes=config.openrouter_catalog_stale_minutes)
                    else "ok"
                )
            catalog_kinds[kind] = {
                "status": kind_status,
                "last_success_at": (
                    (success.completed_at or success.started_at).isoformat() if success else None
                ),
                "last_attempt_at": attempt.started_at.isoformat() if attempt else None,
                "last_error_category": (
                    attempt.error_category if attempt and attempt.status == "failed" else None
                ),
            }
        pending = int(
            db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.usage_status == "pending"))
            or 0
        )
        overdue_before = now - timedelta(minutes=config.openrouter_reconciliation_max_age_minutes)
        overdue = int(
            db.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.usage_status == "pending", UsageEvent.created_at < overdue_before)
            )
            or 0
        )
        account = db.get(OpenRouterAccountState, 1)
    kind_statuses = {item["status"] for item in catalog_kinds.values()}
    catalog_status = "missing" if "missing" in kind_statuses else "stale" if "stale" in kind_statuses else "ok"
    return {
        "configured": bool(config.openrouter_api_key),
        "catalog": {
            "status": catalog_status,
            "kinds": catalog_kinds,
        },
        "account": {
            "status": account.status if account else "unknown",
            "management_key_configured": bool(config.openrouter_management_key),
            "credits_available": bool(account and account.total_credits_microusd is not None),
            "checked_at": account.checked_at.isoformat() if account and account.checked_at else None,
        },
        "usage_reconciliation": {"pending": pending, "overdue": overdue},
    }


def run_async(coroutine: Any) -> Any:
    return asyncio.run(coroutine)
