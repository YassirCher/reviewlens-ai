from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.common import StrictModel, decode_list_cursor, encode_list_cursor, not_found, require_admin_mutation
from app.admin.jobs import create_job
from app.api.v2.dependencies import get_v2_db, require_admin
from app.cache import get_redis
from app.config import settings
from app.db.models import (
    AdminJob, ModelPolicyVersion, OpenRouterAccountState, OpenRouterCatalogRefresh,
    OpenRouterProviderSnapshot, Workspace,
)
from app.errors import V2Error
from app.knowledge.storage import workspace_root
from app.llmops.catalog import current_endpoints, search_models
from app.llmops.contracts import ModelPolicyDocument
from app.llmops.policies import endpoint_eligibility_reasons
from app.services.admin_auth import AuthenticatedAdmin
from app.services.audit_service import add_audit_event
from app.worker import execute_admin_job_task

router = APIRouter(prefix="/admin", tags=["admin-catalog-jobs"])


class RefreshRequest(StrictModel):
    model_slug: str | None = Field(default=None, min_length=3, max_length=300)


@router.get("/models")
def models(
    q: str | None = Query(default=None, max_length=200),
    author: str | None = None,
    capability: str | None = None,
    provider: str | None = None,
    modality: Literal["chat", "embedding"] = "chat",
    min_context: int | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    availability: Literal["available", "all"] = "available",
    sort: Literal["name", "context", "prompt_price"] = "name",
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_v2_db),
    _: AuthenticatedAdmin = Depends(require_admin),
) -> dict:
    filters = {"q": q, "author": author, "capability": capability, "provider": provider, "modality": modality,
               "min_context": min_context, "max_price": str(max_price) if max_price is not None else None,
               "availability": availability, "sort": sort}
    result = search_models(
        db, model_kind=modality, query=q, author=author, capability=capability,
        provider=provider,
        minimum_context=min_context,
        maximum_prompt_price=max_price / Decimal(1_000_000) if max_price is not None else None,
        available_only=availability == "available", sort=sort,
    )
    rows = result["models"]
    after = decode_list_cursor(cursor, filters)
    if after is not None:
        index = next((i for i, item in enumerate(rows) if item["slug"] == after), None)
        if index is None:
            raise V2Error(422, "invalid_cursor", "The model cursor is no longer valid.")
        rows = rows[index + 1:]
    page = rows[:limit]
    return {"status": result["status"], "stale": result["stale"], "fetched_at": result["fetched_at"],
            "last_error_category": result.get("last_error_category"), "total_count": len(result["models"]),
            "items": page, "next_cursor": encode_list_cursor(page[-1]["slug"], filters) if len(rows) > limit else None}


@router.get("/models/{author}/{slug}/endpoints")
def model_endpoints(author: str, slug: str, policy_version_id: uuid.UUID | None = None,
                    db: Session = Depends(get_v2_db),
                    _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    model_slug = f"{author}/{slug}"
    if len(model_slug) > 300:
        raise not_found("model")
    result = current_endpoints(db, model_slug)
    policy_row = db.get(ModelPolicyVersion, policy_version_id) if policy_version_id else None
    if policy_version_id and (policy_row is None or policy_row.lifecycle != "published"):
        raise not_found("published model policy")
    if policy_row is None:
        policies = list(db.scalars(select(ModelPolicyVersion).where(
            ModelPolicyVersion.lifecycle == "published").order_by(ModelPolicyVersion.published_at.desc())))
        policy_row = next((row for row in policies if model_slug in row.policy.get("models", [])), None)
    if policy_row:
        document = ModelPolicyDocument.model_validate(policy_row.policy)
        result["policy_version_id"] = str(policy_row.id)
        for endpoint in result["endpoints"]:
            reasons = endpoint_eligibility_reasons(endpoint, document)
            endpoint["eligible"] = not reasons
            endpoint["eligibility_reasons"] = reasons
    else:
        result["policy_version_id"] = None
        for endpoint in result["endpoints"]:
            endpoint["eligible"] = None
            endpoint["eligibility_reasons"] = ["no_published_policy_for_model"]
    return result


@router.get("/providers")
def providers(db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    refresh = db.scalar(select(OpenRouterCatalogRefresh).where(
        OpenRouterCatalogRefresh.catalog_kind == "providers", OpenRouterCatalogRefresh.status == "succeeded"
    ).order_by(OpenRouterCatalogRefresh.completed_at.desc()).limit(1))
    if refresh is None:
        return {"status": "missing", "items": [], "fetched_at": None}
    rows = list(db.scalars(select(OpenRouterProviderSnapshot).where(
        OpenRouterProviderSnapshot.refresh_id == refresh.id
    ).order_by(OpenRouterProviderSnapshot.slug)))
    attempt = db.scalar(select(OpenRouterCatalogRefresh).where(
        OpenRouterCatalogRefresh.catalog_kind == "providers"
    ).order_by(OpenRouterCatalogRefresh.started_at.desc()).limit(1))
    completed = refresh.completed_at or refresh.started_at
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    stale = (datetime.now(timezone.utc) - completed > timedelta(minutes=settings.openrouter_catalog_stale_minutes)
             or bool(attempt and attempt.id != refresh.id and attempt.status == "failed"))
    return {"status": "stale" if stale else "ok", "stale": stale,
            "fetched_at": refresh.completed_at.isoformat() if refresh.completed_at else None,
            "last_error_category": attempt.error_category if attempt and attempt.status == "failed" else None,
            "items": [{"slug": row.slug, "name": row.name, "privacy": row.privacy, "status": row.status}
                      for row in rows]}


@router.get("/providers/{provider_slug}")
def provider_detail(provider_slug: str, db: Session = Depends(get_v2_db),
                    _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    if not provider_slug or len(provider_slug) > 200:
        raise not_found("provider")
    refresh = db.scalar(select(OpenRouterCatalogRefresh).where(
        OpenRouterCatalogRefresh.catalog_kind == "providers",
        OpenRouterCatalogRefresh.status == "succeeded",
    ).order_by(OpenRouterCatalogRefresh.completed_at.desc()).limit(1))
    if refresh is None:
        raise not_found("provider")
    row = db.scalar(select(OpenRouterProviderSnapshot).where(
        OpenRouterProviderSnapshot.refresh_id == refresh.id,
        OpenRouterProviderSnapshot.slug == provider_slug,
    ))
    if row is None:
        raise not_found("provider")
    return {"slug": row.slug, "name": row.name, "privacy": row.privacy,
            "status": row.status, "metadata": row.metadata_json,
            "fetched_at": row.fetched_at.isoformat()}


@router.get("/models/credits")
def model_credits(db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    if not settings.openrouter_management_key:
        return {"status": "not_configured", "checked_at": None, "remaining_microusd": None}
    account = db.get(OpenRouterAccountState, 1)
    if account is None or account.total_credits_microusd is None or account.total_usage_microusd is None:
        return {"status": account.status if account else "unavailable", "checked_at": account.checked_at.isoformat() if account and account.checked_at else None,
                "remaining_microusd": None}
    stale = account.checked_at is None or datetime.now(timezone.utc) - account.checked_at > timedelta(
        minutes=settings.openrouter_credit_refresh_minutes * 3)
    return {"status": "stale" if stale else account.status,
            "checked_at": account.checked_at.isoformat() if account.checked_at else None,
            "total_credits_microusd": account.total_credits_microusd,
            "total_usage_microusd": account.total_usage_microusd,
            "remaining_microusd": account.total_credits_microusd - account.total_usage_microusd}


@router.post("/models/refresh", status_code=202)
def refresh_models(payload: RefreshRequest, request: Request,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                   db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    if not idempotency_key:
        raise V2Error(422, "idempotency_key_required", "Idempotency-Key is required.")
    existing = db.scalar(select(AdminJob).where(
        AdminJob.actor_id == admin.admin.id, AdminJob.idempotency_key == idempotency_key))
    if existing:
        if existing.kind != "catalog_refresh" or existing.target_id != payload.model_slug:
            raise V2Error(409, "idempotency_conflict", "Idempotency-Key was used for another admin job.")
        return {"job_id": str(existing.id), "status": existing.status}
    try:
        acquired = get_redis().set("reviewlens:admin:catalog-refresh", "1", nx=True,
                                   ex=settings.openrouter_manual_refresh_cooldown_seconds)
    except RedisError as exc:
        raise V2Error(503, "refresh_unavailable", "Catalog refresh is temporarily unavailable.", retryable=True) from exc
    if not acquired:
        raise V2Error(429, "catalog_refresh_throttled", "Wait before refreshing the catalog again.", retryable=True)
    try:
        job = create_job(db, actor_id=admin.admin.id, kind="catalog_refresh",
                         target_id=payload.model_slug, idempotency_key=idempotency_key)
    except ValueError as exc:
        raise V2Error(409, "idempotency_conflict", str(exc)) from exc
    add_audit_event(db, action="catalog.refresh_requested", actor_type="admin", actor_id=admin.admin.id,
                    target_type="openrouter_catalog", target_id=payload.model_slug,
                    request_id=request.state.request_id)
    db.commit()
    try:
        execute_admin_job_task.delay(str(job.id))
    except Exception:
        pass  # The scheduler recovers durable queued jobs.
    return {"job_id": str(job.id), "status": job.status}


@router.get("/jobs/{job_id}")
def read_job(job_id: uuid.UUID, db: Session = Depends(get_v2_db),
             _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    job = db.get(AdminJob, job_id)
    if job is None:
        raise not_found("job")
    return {"id": str(job.id), "kind": job.kind, "status": job.status,
            "target_id": job.target_id, "safe_result": job.safe_result,
            "error_code": job.error_code,
            "created_at": job.created_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None}


@router.get("/jobs/{job_id}/download")
def download_export(job_id: uuid.UUID, db: Session = Depends(get_v2_db),
                    _: AuthenticatedAdmin = Depends(require_admin)) -> FileResponse:
    job = db.get(AdminJob, job_id)
    if job is None or job.kind != "workspace_export" or job.status != "succeeded" or not job.target_id:
        raise not_found("export")
    workspace_id = uuid.UUID(job.target_id)
    if db.get(Workspace, workspace_id) is None:
        raise not_found("workspace")
    path = workspace_root(workspace_id) / "exports" / f"{workspace_id}.zip"
    if not path.is_file():
        raise not_found("export")
    return FileResponse(path, filename=f"reviewlens-workspace-{workspace_id}.zip", media_type="application/zip",
                        headers={"Cache-Control": "private, no-store"})
