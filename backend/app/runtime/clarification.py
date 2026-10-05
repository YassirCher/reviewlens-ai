"""Committed human input at a declared discovery gate, never an agent-created task."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActiveConfiguration, AnalysisRun, ProductClarification, TaskRun
from app.errors import V2Error
from app.public.contracts import ClarificationChoice, ProductClarification as PublicClarification
from app.public.intent import RESOLVER_VERSION
from app.runtime.contracts import RunStatus, TaskStatus


def pending_for_run(db: Session, run_id: uuid.UUID) -> PublicClarification | None:
    row = db.scalar(select(ProductClarification).where(ProductClarification.run_id == run_id,
                                                     ProductClarification.status == "pending"))
    if row is None:
        return None
    return PublicClarification(id=row.id, question=row.question, choices=row.choices,
        expires_at=row.expires_at, resolver_version=row.resolver_version)


def pause_for_product(db: Session, run: AnalysisRun, task: TaskRun, choices: list[dict]) -> None:
    from app.runtime.service import _set_run_status, _set_task_status, append_progress, utc_now
    if task.handler != "analysis.resolve_discovered_product" or not 1 <= len(choices) <= 5:
        raise ValueError("invalid clarification gate")
    validated = [ClarificationChoice.model_validate(choice).model_dump(mode="json") for choice in choices]
    if len({choice["id"] for choice in validated}) != len(validated):
        raise ValueError("duplicate clarification choices")
    if db.scalar(select(ProductClarification.id).where(ProductClarification.run_id == run.id)):
        raise ValueError("one discovery clarification is permitted per run")
    now = utc_now()
    db.add(ProductClarification(id=uuid.uuid4(), run_id=run.id, task_run_id=task.id,
        question="The discovered reviews describe related models. Which exact product should this run analyze?",
        choices=validated, resolver_version=RESOLVER_VERSION, status="pending",
        expires_at=min(run.deadline_at, now + timedelta(minutes=15))))
    _set_task_status(task, TaskStatus.WAITING_FOR_INPUT)
    _set_run_status(run, RunStatus.WAITING_FOR_INPUT)
    append_progress(db, run, event_type="run.waiting_for_input", task=task,
        label="Your clarification is needed", detail="Research is paused until you choose a product model.")


def expire_pending(db: Session, run: AnalysisRun) -> bool:
    from app.runtime.service import _close_budget, _set_run_status, _set_task_status, append_progress, utc_now
    row = db.scalar(select(ProductClarification).where(ProductClarification.run_id == run.id,
        ProductClarification.status == "pending").with_for_update())
    now = utc_now()
    if row is None or now < min(row.expires_at, run.deadline_at):
        return False
    row.status = "expired"
    for task in db.scalars(select(TaskRun).where(TaskRun.run_id == run.id).with_for_update()):
        if task.status == TaskStatus.WAITING_FOR_INPUT:
            _set_task_status(task, TaskStatus.FAILED)
            task.completed_at = now
        elif task.status in {TaskStatus.BLOCKED, TaskStatus.QUEUED}:
            _set_task_status(task, TaskStatus.SKIPPED if task.status == TaskStatus.BLOCKED else TaskStatus.CANCELLED)
            task.completed_at = now
    _set_run_status(run, RunStatus.FAILED)
    run.error_code = "product_clarification_expired"
    run.completed_at = now
    _close_budget(db, run.id)
    append_progress(db, run, event_type="run.failed", label="Clarification time expired",
        detail="No answer was received before the research deadline. Completed work was preserved.")
    return True


def answer_product(db: Session, run_id: uuid.UUID, clarification_id: uuid.UUID, choice_id: str) -> AnalysisRun:
    from app.runtime.service import _set_run_status, _set_task_status, _unblock_dependents, append_progress, utc_now
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
    row = db.scalar(select(ProductClarification).where(ProductClarification.id == clarification_id,
        ProductClarification.run_id == run_id).with_for_update())
    if run is None or row is None:
        raise V2Error(404, "not_found", "The requested resource was not found.")
    if row.status == "answered":
        if row.answer != choice_id:
            raise V2Error(409, "clarification_conflict", "This question has already received a different answer.")
        return run
    if run.status != RunStatus.WAITING_FOR_INPUT or row.status != "pending":
        raise V2Error(409, "clarification_stale", "This clarification is no longer available.")
    if expire_pending(db, run):
        db.commit()
        raise V2Error(409, "clarification_expired", "The time to answer this clarification has expired.")
    active = db.get(ActiveConfiguration, 1)
    if active is None or active.kill_switch:
        raise V2Error(503, "analysis_paused", "Research is temporarily paused. Your answer has not been submitted.")
    chosen = next((choice for choice in row.choices if choice["id"] == choice_id), None)
    if chosen is None:
        raise V2Error(422, "clarification_choice_invalid", "Choose one of the models shown for this question.")
    task = db.get(TaskRun, row.task_run_id)
    if task is None or task.status != TaskStatus.WAITING_FOR_INPUT:
        raise V2Error(409, "clarification_stale", "This clarification is no longer available.")
    now = utc_now()
    row.status, row.answer, row.answered_at = "answered", choice_id, now
    run.resolved_product_name = chosen["product_name"]
    run.resolved_canonical_product = chosen["product_name"].casefold()
    _set_run_status(run, RunStatus.RUNNING)
    _set_task_status(task, TaskStatus.SUCCEEDED)
    task.completed_at = now
    append_progress(db, run, event_type="run.input_received", task=task,
        label="Product confirmed", detail="Research will continue for the model you selected.")
    _unblock_dependents(db, run, task.id)
    return run
