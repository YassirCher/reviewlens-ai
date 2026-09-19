from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.admin.common import decode_cursor, decode_list_cursor, encode_cursor, encode_list_cursor
from app.admin.configuration import BudgetDocument
from app.admin.evaluation import INJECTION_MARKER, _checks, golden_fixture
from app.analysis.registry import AGENT_REGISTRY
from app.errors import V2Error
from app.llmops.contracts import ModelPolicyDocument
from app.llmops.policies import endpoint_eligibility_reasons


def test_admin_cursors_are_signed_and_bound_to_filters() -> None:
    identifier = uuid.uuid4()
    instant = datetime(2026, 9, 18, tzinfo=timezone.utc)
    filters = {"status": "failed", "q": "headphones"}
    cursor = encode_cursor(instant, identifier, filters)
    assert decode_cursor(cursor, filters) == (instant, identifier)
    assert decode_list_cursor(encode_list_cursor("deepseek/deepseek-v4-flash", filters), filters) == "deepseek/deepseek-v4-flash"

    with pytest.raises(V2Error) as changed_filter:
        decode_cursor(cursor, {"status": "complete", "q": "headphones"})
    assert changed_filter.value.code == "invalid_cursor"

    body, signature = cursor.split(".")
    tampered = f"{body}.{('A' if signature[0] != 'A' else 'B')}{signature[1:]}"
    with pytest.raises(V2Error) as changed_signature:
        decode_cursor(tampered, filters)
    assert changed_signature.value.code == "invalid_cursor"


def test_role_fixtures_validate_and_critical_checks_reject_injection_and_missing_evidence() -> None:
    for role, spec in AGENT_REGISTRY.items():
        spec.input_model.model_validate(golden_fixture(role))

    assert _checks("research_coordinator", {"canonical_label": "aurora headphones"})["product_preserved"]
    assert not _checks("research_coordinator", {"canonical_label": "aurora headphones", "note": INJECTION_MARKER})["injection_resisted"]
    assert not _checks("review_analyst", {"claims": [{"central": True, "evidence": []}]})["central_claim_evidence_linkage"]
    assert not _checks("review_analyst", {"claims": [], "evidence_quality_score": 20})["evidence_quality_threshold"]
    assert not _checks("source_curator", {"decisions": [{"video_id": "fixture01", "eligible": True,
        "classification": "review", "product_relevance": 0.9, "independence": 0.1}],
        "ordered_video_ids": ["fixture01"]})["review_quality_threshold"]
    assert not _checks("quality_auditor", {"verdict": "pass", "issues": []})["unsupported_claim_rejected"]


def test_budget_document_rejects_bad_limits() -> None:
    base = {
        "public_runs_per_hour": 2, "public_runs_per_day": 10,
        "public_concurrent_runs": 1, "public_queue_capacity": 10,
        "public_run_cost_cap_usd": Decimal("0.10"),
        "public_daily_cost_cap_usd": Decimal("1.00"),
        "min_video_count": 3, "default_video_count": 5, "max_video_count": 8,
        "token_limits": {"task_total_tokens": {"review": 5000}},
    }
    BudgetDocument.model_validate(base)
    with pytest.raises(ValueError):
        BudgetDocument.model_validate(base | {"default_video_count": 9})
    with pytest.raises(ValueError):
        BudgetDocument.model_validate(base | {"token_limits": {"task_total_tokens": {"review": -1}}})


def test_endpoint_eligibility_explains_routing_rejection() -> None:
    policy = ModelPolicyDocument.model_validate({
        "name": "eligibility fixture", "purpose": "test endpoint explanations",
        "models": ["deepseek/deepseek-v4-flash"],
        "provider": {"mode": "restricted", "only": ["allowed-provider"],
                     "max_price": {"prompt": "0.50"}, "data_collection": "deny"},
        "compatibility_mode": "strict", "minimum_context_tokens": 4000,
        "max_completion_tokens": 1000,
    })
    endpoint = {
        "provider_slug": "other-provider", "status": "offline",
        "supported_parameters": [], "context_length": 1000,
        "max_completion_tokens": 500, "quantization": None,
        "privacy": {"data_collection": True}, "pricing": {"prompt": "0.000001"},
    }
    reasons = endpoint_eligibility_reasons(endpoint, policy)
    assert {"endpoint_unavailable", "provider_not_allowed", "strict_json_unsupported",
            "context_too_small", "completion_too_small", "privacy_policy_conflict",
            "prompt_price_over_cap"} <= set(reasons)
