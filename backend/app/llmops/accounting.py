from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AnalysisRun,
    BudgetPolicyVersion,
    BudgetReservation,
    ConfigurationSnapshot,
    DailyBudgetState,
    OpenRouterAccountState,
    RunBudgetState,
    TaskAttempt,
    TaskRun,
    UsageEvent,
)
from app.db.session import session_scope
from app.llmops.contracts import InvocationContext, NormalizedUsage, OpenRouterErrorCategory


class BudgetRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def usd_to_microusd(value: Decimal) -> int:
    return int((value * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def key_fingerprint(config: Settings = settings) -> str:
    if not config.openrouter_api_key:
        return ""
    key = (config.session_secret or "reviewlens-local-fingerprint").encode("utf-8")
    return hmac.new(key, config.openrouter_api_key.encode("utf-8"), hashlib.sha256).hexdigest()


def _account_state(db: Session, config: Settings, *, lock: bool = False) -> OpenRouterAccountState:
    statement = select(OpenRouterAccountState).where(OpenRouterAccountState.id == 1)
    if lock:
        statement = statement.with_for_update()
    state = db.scalar(statement)
    fingerprint = key_fingerprint(config)
    if state is None:
        state = OpenRouterAccountState(
            id=1,
            environment=config.app_env,
            status="unknown",
            key_fingerprint=fingerprint or None,
        )
        db.add(state)
        db.flush()
    elif state.environment != config.app_env:
        raise RuntimeError("OpenRouter account state belongs to a different APP_ENV")
    elif fingerprint and state.key_fingerprint != fingerprint:
        state.key_fingerprint = fingerprint
        state.status = "unknown"
        state.error_category = None
        state.error_code = None
    return state


def update_account_state(
    *,
    category: OpenRouterErrorCategory | None = None,
    error_code: str | None = None,
    credits_microusd: int | None = None,
    usage_microusd: int | None = None,
    config: Settings = settings,
) -> None:
    now = utc_now()
    with session_scope() as db:
        state = _account_state(db, config, lock=True)
        state.checked_at = now
        if category is None:
            state.status = "healthy"
            state.last_success_at = now
            state.error_category = None
            state.error_code = None
        else:
            state.status = {
                OpenRouterErrorCategory.AUTHENTICATION: "authentication_blocked",
                OpenRouterErrorCategory.PAYMENT_REQUIRED: "payment_blocked",
            }.get(category, "unavailable")
            state.last_failure_at = now
            state.error_category = category.value
            state.error_code = error_code[:120] if error_code else None
        if credits_microusd is not None:
            state.total_credits_microusd = credits_microusd
        if usage_microusd is not None:
            state.total_usage_microusd = usage_microusd


def reset_account_block(config: Settings = settings) -> None:
    with session_scope() as db:
        state = _account_state(db, config, lock=True)
        state.status = "unknown"
        state.error_category = None
        state.error_code = None
        state.checked_at = utc_now()


def _effective_sum(
    db: Session,
    *,
    run_id: uuid.UUID,
    task_run_id: uuid.UUID | None = None,
    agent_version_id: uuid.UUID | None = None,
) -> tuple[int, int]:
    token_value = case(
        (BudgetReservation.status == "reserved", BudgetReservation.estimated_tokens),
        else_=func.coalesce(BudgetReservation.actual_tokens, 0),
    )
    cost_value = case(
        (BudgetReservation.status == "reserved", BudgetReservation.estimated_cost_microusd),
        else_=func.coalesce(BudgetReservation.actual_cost_microusd, 0),
    )
    statement = select(func.coalesce(func.sum(token_value), 0), func.coalesce(func.sum(cost_value), 0)).where(
        BudgetReservation.run_id == run_id
    )
    if task_run_id:
        statement = statement.where(BudgetReservation.task_run_id == task_run_id)
    if agent_version_id:
        statement = statement.where(BudgetReservation.agent_version_id == agent_version_id)
    values = db.execute(statement).one()
    return int(values[0]), int(values[1])


def _limit_value(limits: dict[str, Any], name: str, key: str | None = None) -> int | None:
    raw = limits.get(name)
    if isinstance(raw, dict) and key:
        raw = raw.get(key, raw.get("default"))
    if raw is None:
        return None
    value = int(raw)
    if value < 0:
        raise RuntimeError(f"budget limit {name} cannot be negative")
    return value


def reserve_request(
    context: InvocationContext,
    *,
    operation: str,
    retry_number: int,
    estimated_tokens: int,
    estimated_cost_microusd: int,
    requested_models: list[str],
    config: Settings = settings,
) -> tuple[uuid.UUID, str]:
    if estimated_tokens < 0 or estimated_cost_microusd < 0:
        raise ValueError("estimated usage cannot be negative")
    reservation_id = uuid.uuid4()
    request_id = uuid.uuid4().hex
    now = utc_now()
    with session_scope() as db:
        active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
        if active is None or active.kill_switch:
            raise BudgetRejected("emergency_kill_switch")
        account = _account_state(db, config, lock=True)
        if account.status == "authentication_blocked":
            raise BudgetRejected("openrouter_authentication_blocked")
        if account.status == "payment_blocked":
            raise BudgetRejected("openrouter_payment_blocked")
        if (
            account.total_credits_microusd is not None
            and account.total_usage_microusd is not None
            and account.total_credits_microusd - account.total_usage_microusd
            < estimated_cost_microusd
        ):
            raise BudgetRejected("openrouter_known_credit_balance_insufficient")

        run = db.get(AnalysisRun, context.run_id)
        task = db.get(TaskRun, context.task_run_id)
        attempt = db.get(TaskAttempt, context.task_attempt_id)
        snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id) if run else None
        if not run or not task or not attempt or not snapshot:
            raise RuntimeError("usage attribution records are missing")
        if task.run_id != run.id or attempt.task_run_id != task.id:
            raise RuntimeError("usage attribution does not belong to one run/task/attempt")
        if task.agent_version_id != context.agent_version_id:
            raise RuntimeError("usage agent attribution does not match the task")
        if snapshot.workflow_version_id != context.workflow_version_id:
            raise RuntimeError("usage workflow attribution does not match the run snapshot")
        if context.model_policy_version_id not in {
            uuid.UUID(item["id"]) for item in snapshot.snapshot.get("model_policies", [])
        }:
            raise RuntimeError("usage model policy is not present in the run snapshot")
        if attempt.status != "running":
            raise RuntimeError("paid calls require a running task attempt")
        if now >= context.deadline_at:
            raise BudgetRejected("paid_call_deadline_elapsed")

        budget = db.scalar(
            select(RunBudgetState).where(RunBudgetState.run_id == run.id).with_for_update()
        )
        if budget is None or budget.status != "active":
            raise BudgetRejected("run_budget_exhausted")
        if budget.max_tokens is not None and budget.reserved_tokens + budget.consumed_tokens + estimated_tokens > budget.max_tokens:
            budget.status = "exhausted"
            raise BudgetRejected("run_token_budget_exceeded")
        if budget.max_cost_microusd is not None and budget.reserved_cost_microusd + budget.consumed_cost_microusd + estimated_cost_microusd > budget.max_cost_microusd:
            budget.status = "exhausted"
            raise BudgetRejected("run_cost_budget_exceeded")

        policy = db.get(BudgetPolicyVersion, budget.budget_policy_version_id)
        if policy is None:
            raise RuntimeError("run budget policy is missing")
        limits = policy.token_limits or {}
        task_tokens, task_cost = _effective_sum(db, run_id=run.id, task_run_id=task.id)
        task_token_cap = _limit_value(limits, "task_total_tokens", task.workflow_task_key)
        task_cost_cap = _limit_value(limits, "task_cost_microusd", task.workflow_task_key)
        if task_token_cap is not None and task_tokens + estimated_tokens > task_token_cap:
            raise BudgetRejected("task_token_budget_exceeded")
        if task_cost_cap is not None and task_cost + estimated_cost_microusd > task_cost_cap:
            raise BudgetRejected("task_cost_budget_exceeded")
        agent_tokens, agent_cost = _effective_sum(
            db, run_id=run.id, agent_version_id=context.agent_version_id
        )
        agent_key = str(context.agent_version_id)
        agent_token_cap = _limit_value(limits, "agent_total_tokens", agent_key)
        agent_cost_cap = _limit_value(limits, "agent_cost_microusd", agent_key)
        if agent_token_cap is not None and agent_tokens + estimated_tokens > agent_token_cap:
            raise BudgetRejected("agent_token_budget_exceeded")
        if agent_cost_cap is not None and agent_cost + estimated_cost_microusd > agent_cost_cap:
            raise BudgetRejected("agent_cost_budget_exceeded")

        daily: DailyBudgetState | None = None
        if run.initiator_type == "public":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"public:{now.date().isoformat()}"})
            daily = db.scalar(
                select(DailyBudgetState)
                .where(DailyBudgetState.budget_date == now.date(), DailyBudgetState.scope == "public")
                .with_for_update()
            )
            cap = usd_to_microusd(policy.public_daily_cost_cap_usd)
            if daily is None:
                daily = DailyBudgetState(
                    budget_date=now.date(),
                    scope="public",
                    budget_policy_version_id=policy.id,
                    max_cost_microusd=cap,
                )
                db.add(daily)
                db.flush()
            else:
                daily.max_cost_microusd = min(daily.max_cost_microusd, cap)
            if daily.reserved_cost_microusd + daily.consumed_cost_microusd + estimated_cost_microusd > daily.max_cost_microusd:
                raise BudgetRejected("public_daily_cost_budget_exceeded")
            daily.reserved_cost_microusd += estimated_cost_microusd

        reservation = BudgetReservation(
            id=reservation_id,
            run_id=run.id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            agent_version_id=context.agent_version_id,
            model_policy_version_id=context.model_policy_version_id,
            call_key=context.call_key,
            operation=operation,
            retry_number=retry_number,
            estimated_tokens=estimated_tokens,
            estimated_cost_microusd=estimated_cost_microusd,
            status="reserved",
        )
        usage = UsageEvent(
            id=uuid.uuid4(),
            reservation_id=reservation_id,
            run_id=run.id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            agent_version_id=context.agent_version_id,
            workflow_version_id=context.workflow_version_id,
            model_policy_version_id=context.model_policy_version_id,
            embedding_policy_version_id=context.embedding_policy_version_id,
            call_key=context.call_key,
            operation=operation,
            retry_number=retry_number,
            app_request_id=request_id,
            requested_models=requested_models,
            status="pending",
            usage_status="pending",
            created_at=now,
        )
        db.add_all([reservation, usage])
        budget.reserved_tokens += estimated_tokens
        budget.reserved_cost_microusd += estimated_cost_microusd
    return reservation_id, request_id


def _move_daily_cost(db: Session, reservation: BudgetReservation, actual_cost: int) -> None:
    run = db.get(AnalysisRun, reservation.run_id)
    if run is None or run.initiator_type != "public":
        return
    daily = db.scalar(
        select(DailyBudgetState)
        .where(DailyBudgetState.budget_date == reservation.created_at.date(), DailyBudgetState.scope == "public")
        .with_for_update()
    )
    if daily:
        daily.reserved_cost_microusd = max(0, daily.reserved_cost_microusd - reservation.estimated_cost_microusd)
        daily.consumed_cost_microusd += actual_cost


def finalize_failed_request(
    reservation_id: uuid.UUID,
    *,
    category: OpenRouterErrorCategory,
    error_code: str | None,
) -> None:
    now = utc_now()
    with session_scope() as db:
        reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.id == reservation_id).with_for_update())
        usage = db.scalar(select(UsageEvent).where(UsageEvent.reservation_id == reservation_id).with_for_update())
        if reservation is None or usage is None or reservation.status != "reserved":
            return
        budget = db.scalar(select(RunBudgetState).where(RunBudgetState.run_id == reservation.run_id).with_for_update())
        if budget:
            budget.reserved_tokens = max(0, budget.reserved_tokens - reservation.estimated_tokens)
            budget.reserved_cost_microusd = max(0, budget.reserved_cost_microusd - reservation.estimated_cost_microusd)
        _move_daily_cost(db, reservation, 0)
        reservation.status = "released"
        reservation.actual_tokens = 0
        reservation.actual_cost_microusd = 0
        reservation.reconciled_at = now
        usage.status = "failed"
        usage.usage_status = "complete"
        usage.error_category = category.value
        usage.error_code = error_code[:120] if error_code else None
        usage.completed_at = now


def finalize_successful_request(
    reservation_id: uuid.UUID,
    *,
    generation_id: str | None,
    actual_model: str | None,
    actual_provider: str | None,
    usage_value: NormalizedUsage | None,
    latency_ms: int,
    finish_reason: str | None = None,
    service_tier: str | None = None,
    result_valid: bool = True,
    reconciled: bool = False,
    config: Settings = settings,
) -> None:
    now = utc_now()
    with session_scope() as db:
        reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.id == reservation_id).with_for_update())
        event = db.scalar(select(UsageEvent).where(UsageEvent.reservation_id == reservation_id).with_for_update())
        if reservation is None or event is None or reservation.status != "reserved":
            return
        event.generation_id = generation_id
        event.actual_model = actual_model
        event.actual_provider = actual_provider
        event.latency_ms = latency_ms
        event.finish_reason = finish_reason
        event.service_tier = service_tier
        event.status = "succeeded" if result_valid else "failed"
        if not result_valid:
            event.error_category = "invalid_structured_output"
            event.error_code = "schema_validation_failed"
        if usage_value is None and generation_id:
            event.next_reconciliation_at = now + timedelta(
                seconds=config.openrouter_reconciliation_interval_seconds
            )
            return
        if usage_value is None:
            actual_tokens = reservation.estimated_tokens
            actual_cost = reservation.estimated_cost_microusd
            event.usage_status = "unreconcilable"
        else:
            actual_tokens = usage_value.total_tokens
            actual_cost = usage_value.total_cost_microusd
            event.prompt_tokens = usage_value.prompt_tokens
            event.completion_tokens = usage_value.completion_tokens
            event.reasoning_tokens = usage_value.reasoning_tokens
            event.cached_tokens = usage_value.cached_tokens
            event.cache_write_tokens = usage_value.cache_write_tokens
            event.audio_tokens = usage_value.audio_tokens
            event.total_tokens = usage_value.total_tokens
            event.total_cost_microusd = usage_value.total_cost_microusd
            event.upstream_cost_microusd = usage_value.upstream_cost_microusd
            event.usage_status = "reconciled" if reconciled else "complete"
        _reconcile_locked(db, reservation, actual_tokens=actual_tokens, actual_cost=actual_cost)
        event.completed_at = now


def finalize_unreconcilable_request(reservation_id: uuid.UUID) -> None:
    now = utc_now()
    with session_scope() as db:
        reservation = db.scalar(
            select(BudgetReservation).where(BudgetReservation.id == reservation_id).with_for_update()
        )
        event = db.scalar(
            select(UsageEvent).where(UsageEvent.reservation_id == reservation_id).with_for_update()
        )
        if reservation is None or event is None or reservation.status != "reserved":
            return
        _reconcile_locked(
            db,
            reservation,
            actual_tokens=reservation.estimated_tokens,
            actual_cost=reservation.estimated_cost_microusd,
        )
        event.usage_status = "unreconcilable"
        event.next_reconciliation_at = None
        event.completed_at = now


def _reconcile_locked(db: Session, reservation: BudgetReservation, *, actual_tokens: int, actual_cost: int) -> None:
    budget = db.scalar(select(RunBudgetState).where(RunBudgetState.run_id == reservation.run_id).with_for_update())
    if budget:
        budget.reserved_tokens = max(0, budget.reserved_tokens - reservation.estimated_tokens)
        budget.reserved_cost_microusd = max(0, budget.reserved_cost_microusd - reservation.estimated_cost_microusd)
        budget.consumed_tokens += actual_tokens
        budget.consumed_cost_microusd += actual_cost
        if (
            (budget.max_tokens is not None and budget.consumed_tokens >= budget.max_tokens)
            or (budget.max_cost_microusd is not None and budget.consumed_cost_microusd >= budget.max_cost_microusd)
        ):
            budget.status = "exhausted"
    _move_daily_cost(db, reservation, actual_cost)
    reservation.actual_tokens = actual_tokens
    reservation.actual_cost_microusd = actual_cost
    reservation.status = "reconciled"
    reservation.reconciled_at = utc_now()
