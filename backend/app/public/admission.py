from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AgentVersion,
    AnalysisRun,
    AnonymousSession,
    BudgetPolicyVersion,
    DailyBudgetState,
    ModelPolicyVersion,
    OpenRouterAccountState,
    RunSubmission,
    WorkflowVersion,
)
from app.errors import V2Error
from app.llmops.accounting import usd_to_microusd
from app.llmops.catalog import current_endpoints, search_models
from app.runtime.contracts import WorkflowDag, canonical_json_hash
from app.runtime.service import RuntimeConfigurationError, create_run, normalize_product_name
from app.security import keyed_hash

_RESERVE_SCRIPT = """
local now = tonumber(ARGV[1])
local token = ARGV[2]
for i = 1, 3 do
  local window = tonumber(ARGV[2 + 2*i - 1])
  local limit = tonumber(ARGV[2 + 2*i])
  redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now - window)
  if redis.call('ZCARD', KEYS[i]) >= limit then return i end
end
for i = 1, 3 do
  local window = tonumber(ARGV[2 + 2*i - 1])
  redis.call('ZADD', KEYS[i], now, token)
  redis.call('EXPIRE', KEYS[i], window + 60)
end
return 0
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _signed_identifier(identifier: str, config: Settings) -> str:
    signature = hmac.new(
        config.session_secret.encode(), b"reviewlens-anon-v1:" + identifier.encode(), hashlib.sha256
    ).digest()
    return identifier + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def _parse_cookie(value: str | None, config: Settings) -> str | None:
    if not value or len(value) > 160 or value.count(".") != 1:
        return None
    identifier, signature = value.split(".", 1)
    if len(identifier) != 43 or len(signature) != 43:
        return None
    expected = _signed_identifier(identifier, config).split(".", 1)[1]
    if not hmac.compare_digest(signature, expected):
        return None
    return identifier


def resolve_session(
    db: Session,
    cookie: str | None,
    *,
    create: bool = False,
    config: Settings = settings,
) -> tuple[AnonymousSession | None, str | None]:
    identifier = _parse_cookie(cookie, config)
    now = utc_now()
    if identifier:
        digest = keyed_hash("anon:" + identifier, config.session_secret)
        # Serialize sliding-expiry updates for concurrent tabs/submissions.
        # AnonymousSession uses optimistic versions, so an unlocked read here
        # could turn a valid same-key replay into a StaleDataError/HTTP 500.
        record = db.scalar(
            select(AnonymousSession)
            .where(AnonymousSession.identifier_hash == digest)
            .with_for_update()
        )
        if (
            record is not None
            and record.revoked_at is None
            and record.expires_at is not None
            and record.absolute_expires_at is not None
            and record.expires_at > now
            and record.absolute_expires_at > now
        ):
            record.last_seen_at = now
            record.expires_at = min(
                now + timedelta(hours=config.anonymous_session_idle_hours), record.absolute_expires_at
            )
            return record, None
    if not create:
        return None, None
    identifier = secrets.token_urlsafe(32)
    absolute = now + timedelta(days=config.anonymous_session_absolute_days)
    record = AnonymousSession(
        id=uuid.uuid4(),
        identifier_hash=keyed_hash("anon:" + identifier, config.session_secret),
        quota_counters={},
        last_seen_at=now,
        expires_at=min(now + timedelta(hours=config.anonymous_session_idle_hours), absolute),
        absolute_expires_at=absolute,
    )
    db.add(record)
    db.flush()
    return record, _signed_identifier(identifier, config)


def client_ip_hash(client_host: str, config: Settings = settings) -> str:
    return keyed_hash(client_host, config.rate_limit_hash_secret)


def _limits(db: Session, config: Settings) -> tuple[ActiveConfiguration, BudgetPolicyVersion]:
    active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
    if active is None or active.kill_switch:
        raise V2Error(503, "analysis_unavailable", "New analyses are temporarily unavailable.", retryable=True)
    policy = db.get(BudgetPolicyVersion, active.budget_policy_version_id)
    if policy is None or policy.lifecycle != "published" or active.workflow_version_id is None:
        raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True)
    account = db.get(OpenRouterAccountState, 1)
    if account and account.status in {"authentication_blocked", "payment_blocked"}:
        raise V2Error(503, "analysis_provider_unavailable", "Analysis is temporarily unavailable.", retryable=True)
    return active, policy


def normalize_options(payload: Any, policy: BudgetPolicyVersion) -> tuple[str, dict[str, Any]]:
    try:
        display, _ = normalize_product_name(payload.product_name)
    except ValueError as exc:
        raise V2Error(422, "validation_error", "The request is invalid.", details={"product_name": "invalid"}) from exc
    count = payload.video_count if payload.video_count is not None else policy.default_video_count
    if not policy.min_video_count <= count <= policy.max_video_count:
        raise V2Error(422, "validation_error", "The request is invalid.", details={"video_count": "out_of_range"})
    language = getattr(payload, "locale", None) or "en"
    return display, {"source_count": count, "analyze_comments": payload.analyze_comments, "language": language}


def _quota_state(
    db: Session, policy: BudgetPolicyVersion, session_id: uuid.UUID, ip_hash: str, now: datetime
) -> dict[str, int]:
    hour = now - timedelta(hours=1)
    day = now - timedelta(days=1)
    ip_hour = db.scalar(select(func.count(RunSubmission.id)).where(RunSubmission.ip_hash == ip_hash, RunSubmission.created_at > hour)) or 0
    ip_day = db.scalar(select(func.count(RunSubmission.id)).where(RunSubmission.ip_hash == ip_hash, RunSubmission.created_at > day)) or 0
    session_day = db.scalar(select(func.count(RunSubmission.id)).where(RunSubmission.actor_type == "public", RunSubmission.actor_id == session_id, RunSubmission.created_at > day)) or 0
    active = db.scalar(select(func.count(AnalysisRun.id)).where(AnalysisRun.initiator_type == "public", AnalysisRun.initiator_id == session_id, AnalysisRun.status.in_(("queued", "running", "cancelling")))) or 0
    return {
        "hourly_remaining": max(0, policy.public_runs_per_hour - ip_hour),
        "daily_ip_remaining": max(0, policy.public_runs_per_day - ip_day),
        "daily_session_remaining": max(0, policy.public_runs_per_day - session_day),
        "concurrent_remaining": max(0, policy.public_concurrent_runs - active),
    }


def _price_pair(pricing: dict[str, Any]) -> tuple[Decimal, Decimal]:
    try:
        prompt = Decimal(str(pricing["prompt"]))
        completion = Decimal(str(pricing["completion"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise V2Error(503, "pricing_unavailable", "Analysis pricing is temporarily unavailable.", retryable=True) from exc
    if not prompt.is_finite() or not completion.is_finite() or min(prompt, completion) < 0:
        raise V2Error(503, "pricing_unavailable", "Analysis pricing is temporarily unavailable.", retryable=True)
    return prompt, completion


def _estimate(
    db: Session,
    active: ActiveConfiguration,
    policy: BudgetPolicyVersion,
    options: dict[str, Any],
    config: Settings,
) -> tuple[int, int, str]:
    workflow = db.get(WorkflowVersion, active.workflow_version_id)
    if workflow is None or workflow.lifecycle != "published":
        raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True)
    try:
        dag = WorkflowDag.model_validate(workflow.dag).materialize(
            source_count=options["source_count"], comments_enabled=options["analyze_comments"]
        )
    except ValueError as exc:
        raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True) from exc
    catalog = search_models(db, model_kind="chat", config=config)
    if catalog["status"] != "ok" or catalog["stale"]:
        raise V2Error(503, "catalog_unavailable", "Analysis pricing is temporarily unavailable.", retryable=True)
    models = {item["slug"]: item for item in catalog["models"]}
    total_tokens = 0
    total_cost = Decimal(0)
    price_cache: dict[str, tuple[Decimal, Decimal]] = {}
    for task in dag.tasks:
        if task.agent_version_id is None:
            continue
        agent = db.get(AgentVersion, task.agent_version_id)
        route = db.get(ModelPolicyVersion, agent.model_policy_version_id) if agent else None
        if agent is None or route is None or route.lifecycle != "published":
            raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True)
        input_tokens = int(agent.execution_limits["max_input_tokens"])
        output_tokens = int(agent.generation_config["max_output_tokens"]) + int(agent.generation_config.get("max_reasoning_tokens", 0))
        attempts = max(task.retry.max_attempts, int(agent.execution_limits.get("max_attempts", 1)))
        total_tokens += attempts * (input_tokens + output_tokens)
        route_prices = []
        for slug in route.policy.get("models", []):
            model = models.get(slug)
            if model is None:
                raise V2Error(503, "catalog_unavailable", "Analysis pricing is temporarily unavailable.", retryable=True)
            if slug not in price_cache:
                pairs = [_price_pair(model["pricing"])]
                endpoints = current_endpoints(db, slug, config)
                if endpoints["status"] != "ok" or endpoints["stale"] or not endpoints["endpoints"]:
                    raise V2Error(503, "catalog_unavailable", "Analysis pricing is temporarily unavailable.", retryable=True)
                pairs.extend(_price_pair(item["pricing"]) for item in endpoints["endpoints"])
                price_cache[slug] = (max(pair[0] for pair in pairs), max(pair[1] for pair in pairs))
            route_prices.append(price_cache[slug])
        if not route_prices:
            raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True)
        total_cost += attempts * max(
            Decimal(input_tokens) * prompt + Decimal(output_tokens) * completion
            for prompt, completion in route_prices
        )
    token_cap = int(policy.token_limits.get("run_total_tokens", 0) or 0)
    if token_cap <= 0 or not total_tokens:
        raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True)
    estimated_tokens = min(total_tokens, token_cap)
    estimated_cost = int((total_cost * Decimal("1.25") * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))
    cap = usd_to_microusd(policy.public_run_cost_cap_usd)
    ratio = Decimal(estimated_cost) / Decimal(cap) if cap else Decimal(1)
    band = "low" if ratio < Decimal("0.33") else "medium" if ratio < Decimal("0.66") else "high"
    return estimated_tokens, estimated_cost, band


def preflight(
    db: Session, redis: Redis, payload: Any, session_id: uuid.UUID, ip_hash: str, config: Settings = settings
) -> dict[str, Any]:
    try:
        redis.ping()
    except RedisError as exc:
        raise V2Error(503, "admission_unavailable", "Analysis admission is temporarily unavailable.", retryable=True) from exc
    active, policy = _limits(db, config)
    product, options = normalize_options(payload, policy)
    estimated_tokens, estimated_cost, cost_band = _estimate(db, active, policy, options, config)
    quota = _quota_state(db, policy, session_id, ip_hash, utc_now())
    queued = db.scalar(select(func.count(AnalysisRun.id)).where(AnalysisRun.initiator_type == "public", AnalysisRun.status == "queued")) or 0
    daily = db.get(DailyBudgetState, (utc_now().date(), "public"))
    remaining_cost = usd_to_microusd(policy.public_daily_cost_cap_usd) - (
        daily.consumed_cost_microusd + daily.reserved_cost_microusd if daily else 0
    )
    estimated_max = usd_to_microusd(policy.public_run_cost_cap_usd)
    reasons = []
    if not active.public_analysis_enabled or not config.public_analysis_enabled:
        reasons.append("public_analysis_disabled")
    if not all(quota.values()):
        reasons.append("public_quota_exceeded")
    if queued >= policy.public_queue_capacity:
        reasons.append("queue_full")
    if estimated_max <= 0 or estimated_cost > estimated_max or estimated_cost > remaining_cost:
        reasons.append("public_daily_budget_exceeded")
    return {
        "normalized_options": {"product_name": product, "video_count": options["source_count"], "analyze_comments": options["analyze_comments"], "locale": options["language"]},
        "allowed": not reasons,
        "denial_code": reasons[0] if reasons else None,
        "queue": {"condition": "full" if queued >= policy.public_queue_capacity else "available", "queued_runs": int(queued)},
        "remaining_public_quota": quota,
        "estimate": {"token_band": {"min": 0, "max": estimated_tokens}, "cost_band": cost_band, "non_binding": True},
    }


def _rate_keys(ip_hash: str, session_id: uuid.UUID) -> tuple[str, str, str]:
    return (
        f"reviewlens:public:hour:ip:{ip_hash}",
        f"reviewlens:public:day:ip:{ip_hash}",
        f"reviewlens:public:day:session:{session_id}",
    )


def _reserve_rate(redis: Redis, policy: BudgetPolicyVersion, ip_hash: str, session_id: uuid.UUID, token: str) -> tuple[str, str, str]:
    keys = _rate_keys(ip_hash, session_id)
    try:
        result = redis.eval(
            _RESERVE_SCRIPT,
            3,
            *keys,
            int(time.time()), token,
            3600, policy.public_runs_per_hour,
            86400, policy.public_runs_per_day,
            86400, policy.public_runs_per_day,
        )
    except RedisError as exc:
        raise V2Error(503, "admission_unavailable", "Analysis admission is temporarily unavailable.", retryable=True) from exc
    if result:
        raise V2Error(429, "public_rate_limit_exceeded", "Analysis limit reached. Try again later.", retryable=True, headers={"Retry-After": "3600"})
    return keys


def _release_rate(redis: Redis, keys: tuple[str, str, str], token: str) -> None:
    try:
        for key in keys:
            redis.zrem(key, token)
    except RedisError:
        pass  # Conservative temporary quota use is safer than allowing an extra run.


def create_analysis(
    db: Session,
    redis: Redis | None,
    *,
    payload: Any,
    actor_type: str,
    actor_id: uuid.UUID,
    idempotency_key: str,
    ip_hash: str | None,
    entrypoint: str = "v2_public",
    user_id: uuid.UUID | None = None,
    config: Settings = settings,
) -> AnalysisRun:
    keys: tuple[str, str, str] | None = None
    reservation = uuid.uuid4().hex
    try:
        active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
        policy = db.get(BudgetPolicyVersion, active.budget_policy_version_id) if active else None
        if policy is None or policy.lifecycle != "published":
            raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True)
        product, options = normalize_options(payload, policy)
        request_hash = canonical_json_hash({"product_name": product, **options})
        existing = db.scalar(select(RunSubmission).where(RunSubmission.actor_type == actor_type, RunSubmission.actor_id == actor_id, RunSubmission.idempotency_key == idempotency_key))
        if existing:
            if existing.request_hash != request_hash:
                raise V2Error(409, "idempotency_conflict", "The idempotency key was used for a different request.")
            run = db.get(AnalysisRun, existing.run_id)
            assert run is not None
            if user_id is not None and run.user_id is None:
                run.user_id = user_id
            db.commit()
            return run
        active, policy = _limits(db, config)
        if actor_type == "public":
            if not active.public_analysis_enabled or not config.public_analysis_enabled:
                raise V2Error(503, "public_analysis_disabled", "New public analyses are temporarily unavailable.", retryable=True)
            assert ip_hash is not None and redis is not None
            quota = _quota_state(db, policy, actor_id, ip_hash, utc_now())
            if not all(quota.values()):
                raise V2Error(429, "public_rate_limit_exceeded", "Analysis limit reached. Try again later.", retryable=True, headers={"Retry-After": "3600"})
            queued = db.scalar(select(func.count(AnalysisRun.id)).where(AnalysisRun.initiator_type == "public", AnalysisRun.status == "queued")) or 0
            if queued >= policy.public_queue_capacity:
                raise V2Error(503, "queue_full", "The analysis queue is full. Try again later.", retryable=True, headers={"Retry-After": "60"})
            _, estimated_cost, _ = _estimate(db, active, policy, options, config)
            daily = db.get(DailyBudgetState, (utc_now().date(), "public"))
            remaining = usd_to_microusd(policy.public_daily_cost_cap_usd) - (
                daily.consumed_cost_microusd + daily.reserved_cost_microusd if daily else 0
            )
            if (
                usd_to_microusd(policy.public_run_cost_cap_usd) <= 0
                or estimated_cost > usd_to_microusd(policy.public_run_cost_cap_usd)
                or estimated_cost > remaining
            ):
                raise V2Error(429, "public_daily_budget_exceeded", "The daily analysis budget is exhausted.", retryable=True, headers={"Retry-After": "3600"})
            keys = _reserve_rate(redis, policy, ip_hash, actor_id, reservation)
        run = create_run(
            db,
            product_name=product,
            initiator_type=actor_type,
            initiator_id=actor_id,
            user_id=user_id,
            requested_options={**options, "entrypoint": entrypoint},
        )
        db.add(RunSubmission(id=uuid.uuid4(), actor_type=actor_type, actor_id=actor_id, idempotency_key=idempotency_key, request_hash=request_hash, ip_hash=ip_hash, run_id=run.id))
        db.commit()
        return run
    except RuntimeConfigurationError as exc:
        db.rollback()
        if keys and redis:
            _release_rate(redis, keys, reservation)
        raise V2Error(503, "analysis_not_configured", "Analysis is temporarily unavailable.", retryable=True) from exc
    except Exception:
        db.rollback()
        if keys and redis:
            _release_rate(redis, keys, reservation)
        raise
