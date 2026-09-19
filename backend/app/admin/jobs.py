from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdminJob
from app.db.session import session_scope
from app.llmops.catalog import refresh_catalogs, refresh_model_endpoints
from app.runtime.contracts import canonical_json_hash


def create_job(
    db: Session, *, actor_id: uuid.UUID, kind: str, target_id: str | None,
    idempotency_key: str,
) -> AdminJob:
    if not 16 <= len(idempotency_key) <= 160:
        raise ValueError("Idempotency-Key must contain 16-160 characters")
    request_hash = canonical_json_hash({"kind": kind, "target_id": target_id})
    existing = db.scalar(select(AdminJob).where(AdminJob.actor_id == actor_id, AdminJob.idempotency_key == idempotency_key))
    if existing:
        if existing.request_hash != request_hash:
            raise ValueError("Idempotency-Key was used for another admin job")
        return existing
    job = AdminJob(actor_id=actor_id, kind=kind, target_id=target_id,
                   idempotency_key=idempotency_key, request_hash=request_hash, status="queued")
    db.add(job)
    db.flush()
    return job


def run_job(job_id: uuid.UUID) -> dict:
    with session_scope() as db:
        job = db.scalar(select(AdminJob).where(AdminJob.id == job_id).with_for_update())
        if job is None:
            return {"status": "missing"}
        if job.status != "queued":
            return {"status": job.status}
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        kind, target_id = job.kind, job.target_id
    try:
        if kind == "catalog_refresh":
            if target_id:
                result = asyncio.run(refresh_model_endpoints(target_id))
            else:
                result = asyncio.run(refresh_catalogs(manual=False))
        elif kind == "workspace_export":
            from app.knowledge.service import export_workspace
            with session_scope() as db:
                export_workspace(db, uuid.UUID(target_id or ""))
            result = {"download_url": f"/api/v2/admin/jobs/{job_id}/download"}
        elif kind == "neo4j_rebuild":
            from app.knowledge.projections import rebuild_neo4j_projection
            with session_scope() as db:
                result = rebuild_neo4j_projection(db, uuid.UUID(target_id or ""))
        elif kind == "agent_evaluation":
            from app.admin.evaluation import evaluate_agent_version
            result = evaluate_agent_version(uuid.UUID(target_id or ""), job_id)
        else:
            raise ValueError("unsupported admin job kind")
    except Exception as exc:
        with session_scope() as db:
            job = db.get(AdminJob, job_id)
            if job and job.status == "running":
                job.status = "failed"
                job.error_code = type(exc).__name__[:120]
                job.completed_at = datetime.now(timezone.utc)
        return {"status": "failed", "error_code": type(exc).__name__[:120]}
    with session_scope() as db:
        job = db.get(AdminJob, job_id)
        if job and job.status == "running":
            job.status = "succeeded"
            job.safe_result = result
            job.completed_at = datetime.now(timezone.utc)
    return {"status": "succeeded"}


def recover_jobs() -> int:
    """Recover queued jobs and timed-out leases after worker interruption."""
    from app.worker import execute_admin_job_task

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    with session_scope() as db:
        jobs = list(db.scalars(select(AdminJob).where(
            (AdminJob.status == "queued") | ((AdminJob.status == "running") & (AdminJob.started_at < cutoff))
        ).order_by(AdminJob.created_at).limit(25).with_for_update(skip_locked=True)))
        for job in jobs:
            if job.status == "running":
                job.status = "queued"
                job.started_at = None
        ids = [job.id for job in jobs]
    for job_id in ids:
        execute_admin_job_task.delay(str(job_id))
    return len(ids)
