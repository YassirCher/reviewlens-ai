from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, CompatibilityRequest, CutoverObservation

SCHEMA = "reviewlens.phase12.cutover-evidence.v1"
QUIET_PERIOD = timedelta(hours=24)
ATTESTATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,119}$")
PRODUCTION_THRESHOLDS: dict[str, int | float] = {
    "stable_window_hours": 24,
    "min_terminal_runs": 20,
    "min_compatibility_requests": 5,
    "max_failure_rate": 0.10,
    "max_p95_run_latency_seconds": 900,
    "min_compatibility_success_rate": 0.95,
}
THRESHOLD_FIELDS = frozenset(
    {
        *PRODUCTION_THRESHOLDS,
        "public_run_token_cap",
        "public_run_cost_cap_microusd",
        "public_daily_cost_cap_microusd",
    }
)
SAMPLE_FIELDS = frozenset(
    {
        "terminal_runs",
        "successful_runs",
        "failed_runs",
        "compatibility_requests",
        "mapped_compatibility_requests",
        "published_reports",
        "central_claims",
        "linked_central_claims",
        "usage_events",
    }
)
DISTRIBUTION_FIELDS = frozenset(
    {
        "total_tokens",
        "p50_tokens_per_run",
        "p95_tokens_per_run",
        "total_cost_microusd",
        "p50_cost_microusd_per_run",
        "p95_cost_microusd_per_run",
    }
)


class RetirementEvidenceError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _timestamp(value: str | None, field: str) -> datetime:
    if not value:
        raise RetirementEvidenceError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetirementEvidenceError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise RetirementEvidenceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_production_thresholds(thresholds: dict[str, Any]) -> None:
    minimums = {
        "stable_window_hours",
        "min_terminal_runs",
        "min_compatibility_requests",
        "min_compatibility_success_rate",
    }
    for name, required in PRODUCTION_THRESHOLDS.items():
        if name not in thresholds:
            raise RetirementEvidenceError(f"missing production threshold: {name}")
        observed = thresholds[name]
        valid = observed >= required if name in minimums else observed <= required
        if not valid:
            raise RetirementEvidenceError(f"weakened production threshold: {name}")


def _require_passed_result(result: dict[str, Any]) -> None:
    if result.get("ready") is not True or result.get("blockers") != []:
        raise RetirementEvidenceError("cutover result is not ready")
    metrics = result.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise RetirementEvidenceError("cutover metrics are missing")
    by_code = {item.get("code"): item for item in metrics if isinstance(item, dict)}
    required = {
        "window_age",
        "terminal_runs",
        "compatibility_requests",
        "failure_rate",
        "p95_run_latency",
        "central_claim_evidence",
        "compatibility_mapping",
        "unresolved_usage",
        "budget_breaches",
    }
    missing = required - by_code.keys()
    if missing:
        raise RetirementEvidenceError(f"cutover metrics are incomplete: {', '.join(sorted(missing))}")
    if any(by_code[code].get("passed") is not True for code in required):
        raise RetirementEvidenceError("one or more cutover metrics failed")
    samples = result.get("samples") or {}
    if int(samples.get("terminal_runs", 0)) < 20:
        raise RetirementEvidenceError("fewer than 20 terminal runs were observed")
    if int(samples.get("compatibility_requests", 0)) < 5:
        raise RetirementEvidenceError("fewer than five compatibility requests were observed")


def _safe_numeric_map(value: Any, allowed: frozenset[str], field: str) -> dict[str, int | float | None]:
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise RetirementEvidenceError(f"{field} contains unsupported fields")
    if any(not isinstance(item, (int, float, type(None))) or isinstance(item, bool) for item in value.values()):
        raise RetirementEvidenceError(f"{field} contains a non-numeric value")
    return {key: value[key] for key in sorted(value)}


def _safe_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RetirementEvidenceError("cutover result is missing")
    allowed = {"observed_at", "ready", "blockers", "metrics", "samples", "distributions"}
    if set(value) != allowed:
        raise RetirementEvidenceError("cutover result contains unsupported fields")
    metrics = value.get("metrics")
    if not isinstance(metrics, list):
        raise RetirementEvidenceError("cutover metrics are missing")
    safe_metrics = []
    metric_fields = {"code", "observed_value", "threshold", "passed", "unit"}
    for item in metrics:
        if not isinstance(item, dict) or set(item) != metric_fields:
            raise RetirementEvidenceError("a cutover metric contains unsupported fields")
        if not isinstance(item.get("code"), str) or not isinstance(item.get("unit"), str):
            raise RetirementEvidenceError("a cutover metric label is invalid")
        if not isinstance(item.get("passed"), bool):
            raise RetirementEvidenceError("a cutover metric pass state is invalid")
        for field in ("observed_value", "threshold"):
            if not isinstance(item.get(field), (str, int, float, bool, type(None))):
                raise RetirementEvidenceError("a cutover metric value is invalid")
        safe_metrics.append({field: item[field] for field in sorted(metric_fields)})
    blockers = value.get("blockers")
    if not isinstance(blockers, list) or any(not isinstance(item, str) for item in blockers):
        raise RetirementEvidenceError("cutover blockers are invalid")
    return {
        "observed_at": value.get("observed_at"),
        "ready": value.get("ready"),
        "blockers": blockers,
        "metrics": safe_metrics,
        "samples": _safe_numeric_map(value.get("samples"), SAMPLE_FIELDS, "cutover samples"),
        "distributions": _safe_numeric_map(
            value.get("distributions"), DISTRIBUTION_FIELDS, "cutover distributions"
        ),
    }


def validate_evidence_document(
    document: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if set(document) != {"schema", "payload", "sha256"} or document.get("schema") != SCHEMA:
        raise RetirementEvidenceError("unsupported retirement evidence schema")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise RetirementEvidenceError("retirement evidence payload is missing")
    if set(payload) != {"exported_at", "observation", "compatibility", "attestation", "audit_events"}:
        raise RetirementEvidenceError("retirement evidence payload contains unsupported fields")
    expected = hashlib.sha256(_canonical(payload)).hexdigest()
    if document.get("sha256") != expected:
        raise RetirementEvidenceError("retirement evidence digest does not match")

    observation = payload.get("observation") or {}
    if set(observation) != {
        "id", "environment", "test_evidence", "status", "root_mode", "thresholds", "result",
        "started_at", "evaluated_at", "ended_at", "version",
    }:
        raise RetirementEvidenceError("retirement observation contains unsupported fields")
    if observation.get("status") != "passed" or observation.get("test_evidence") is not False:
        raise RetirementEvidenceError("a passed non-test observation is required")
    if observation.get("root_mode") != "v2":
        raise RetirementEvidenceError("the passed observation did not use the V2 root")
    try:
        uuid.UUID(str(observation.get("id")))
    except ValueError as exc:
        raise RetirementEvidenceError("the observation identifier is invalid") from exc
    if not isinstance(observation.get("environment"), str) or not observation["environment"]:
        raise RetirementEvidenceError("the observation environment is invalid")
    if not isinstance(observation.get("version"), int) or observation["version"] < 1:
        raise RetirementEvidenceError("the observation version is invalid")
    started = _timestamp(observation.get("started_at"), "observation.started_at")
    evaluated = _timestamp(observation.get("evaluated_at"), "observation.evaluated_at")
    ended = _timestamp(observation.get("ended_at"), "observation.ended_at")
    if not started <= evaluated <= ended:
        raise RetirementEvidenceError("the observation timestamps are inconsistent")
    if ended - started < timedelta(hours=24):
        raise RetirementEvidenceError("the production observation is shorter than 24 hours")
    thresholds = observation.get("thresholds") or {}
    if not isinstance(thresholds, dict) or not set(thresholds).issubset(THRESHOLD_FIELDS):
        raise RetirementEvidenceError("retirement thresholds contain unsupported fields")
    _require_production_thresholds(thresholds)
    result = _safe_result(observation.get("result"))
    _require_passed_result(result)
    result_observed = _timestamp(result.get("observed_at"), "observation.result.observed_at")
    if result_observed != evaluated or evaluated != ended:
        raise RetirementEvidenceError("the final observation result is stale")

    compatibility = payload.get("compatibility") or {}
    if set(compatibility) != {"last_request_at", "quiet_period_hours", "observed_quiet_hours"}:
        raise RetirementEvidenceError("compatibility evidence contains unsupported fields")
    last_request = _timestamp(compatibility.get("last_request_at"), "compatibility.last_request_at")
    exported = _timestamp(payload.get("exported_at"), "exported_at")
    validation_time = now or utc_now()
    if exported > validation_time + timedelta(minutes=5):
        raise RetirementEvidenceError("retirement evidence was exported in the future")
    if exported - max(ended, last_request) < QUIET_PERIOD:
        raise RetirementEvidenceError("the 24-hour compatibility quiet period has not elapsed")
    if compatibility.get("quiet_period_hours") != 24:
        raise RetirementEvidenceError("the compatibility quiet period is not 24 hours")
    if float(compatibility.get("observed_quiet_hours", 0)) < 24:
        raise RetirementEvidenceError("the recorded compatibility quiet period is incomplete")

    attestation = payload.get("attestation") or {}
    if set(attestation) != {"confirmed_no_active_consumers", "reference"}:
        raise RetirementEvidenceError("operator attestation contains unsupported fields")
    reference = attestation.get("reference")
    if attestation.get("confirmed_no_active_consumers") is not True:
        raise RetirementEvidenceError("consumer migration was not attested")
    if not isinstance(reference, str) or not ATTESTATION_PATTERN.fullmatch(reference):
        raise RetirementEvidenceError("the operator attestation reference is invalid")
    audit_events = payload.get("audit_events")
    if not isinstance(audit_events, list) or any(
        not isinstance(item, dict)
        or set(item) != {"id", "action", "created_at"}
        or not all(isinstance(item[field], str) for field in ("id", "action", "created_at"))
        for item in audit_events
    ):
        raise RetirementEvidenceError("audit evidence contains unsupported fields")
    if any(item["action"] == "cutover.rollback_recorded" for item in audit_events):
        raise RetirementEvidenceError("a rollback was recorded for the observation")
    return payload


def build_evidence_document(
    db: Session,
    observation_id: uuid.UUID,
    *,
    attestation_reference: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not ATTESTATION_PATTERN.fullmatch(attestation_reference):
        raise RetirementEvidenceError("the operator attestation reference is invalid")
    observation = db.get(CutoverObservation, observation_id)
    if observation is None:
        raise RetirementEvidenceError("cutover observation was not found")
    last_request_at = db.scalar(select(func.max(CompatibilityRequest.started_at)))
    if last_request_at is None:
        raise RetirementEvidenceError("compatibility request history is missing")
    audit_events = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.target_type == "cutover_observation",
                AuditEvent.target_id == str(observation.id),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    exported_at = now or utc_now()
    thresholds = _safe_numeric_map(observation.thresholds, THRESHOLD_FIELDS, "cutover thresholds")
    result = _safe_result(observation.latest_result)
    payload = {
        "exported_at": exported_at.isoformat(),
        "observation": {
            "id": str(observation.id),
            "environment": observation.environment,
            "test_evidence": observation.test_evidence,
            "status": observation.status,
            "root_mode": observation.root_mode,
            "thresholds": thresholds,
            "result": result,
            "started_at": observation.started_at.isoformat(),
            "evaluated_at": observation.evaluated_at.isoformat() if observation.evaluated_at else None,
            "ended_at": observation.ended_at.isoformat() if observation.ended_at else None,
            "version": observation.version,
        },
        "compatibility": {
            "last_request_at": last_request_at.isoformat(),
            "quiet_period_hours": 24,
            "observed_quiet_hours": round(
                (exported_at - max(observation.ended_at or exported_at, last_request_at)).total_seconds() / 3600,
                4,
            ),
        },
        "attestation": {
            "confirmed_no_active_consumers": True,
            "reference": attestation_reference,
        },
        "audit_events": [
            {"id": str(event.id), "action": event.action, "created_at": event.created_at.isoformat()}
            for event in audit_events
        ],
    }
    document = {"schema": SCHEMA, "payload": payload, "sha256": hashlib.sha256(_canonical(payload)).hexdigest()}
    validate_evidence_document(document)
    return document


def write_evidence_document(document: dict[str, Any], output: Path) -> None:
    validate_evidence_document(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)


def read_evidence_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetirementEvidenceError("retirement evidence could not be read") from exc
    if not isinstance(document, dict):
        raise RetirementEvidenceError("retirement evidence must be a JSON object")
    validate_evidence_document(document)
    return document
