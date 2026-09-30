from __future__ import annotations

import uuid
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.analysis.contracts import AuditResult, FinalReportDraft
from app.analysis import executor
from app.analysis.executor import _score
from app.analysis.grounding import ground_report
from app.analysis.registry import AGENT_REGISTRY
from app.knowledge.retrieval import estimate_tokens
from app.public.reports import _decision_guide
from app.tools import evidence as evidence_tool
from app.tools.contracts import EvidenceValidateInput
from app.tools.evidence import comment_excerpt_matches, transcript_excerpt_matches
from app.tools.youtube import balance_curated_order
from app.runtime.service import RuntimeTaskError


_BUDGET_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compare_buying_report_budget.py"


def _fixture() -> tuple[FinalReportDraft, list[dict]]:
    first, second = uuid.uuid4(), uuid.uuid4()
    first_evidence, second_evidence = uuid.uuid4(), uuid.uuid4()
    reviews = [
        {
            "source_id": str(source_id), "channel_id": f"channel-{index}",
            "source_score": 75, "reviewer_sentiment_score": 75,
            "purchase_recommendation_score": 75, "evidence_quality_score": 80,
            "review_type": "long_term", "translated": False,
            "claims": [{"claim": "The battery lasted 10 hours under light use.", "central": True,
                        "evidence": [{"evidence_node_id": str(evidence_id),
                                      "evidence_text": "Battery lasted 10 hours under light use.",
                                      "support_type": "supports"}]}],
        }
        for index, (source_id, evidence_id) in enumerate(((first, first_evidence), (second, second_evidence)))
    ]
    draft = FinalReportDraft.model_validate({
        "product_display_name": "Test laptop", "product_canonical_name": "Test laptop",
        "summary": "Battery lasts 10 hours under light use.",
        "consensus_pros": [{"statement": "Battery lasted 10 hours under light use.",
                            "source_ids": [str(first), str(second)],
                            "evidence_node_ids": [str(first_evidence), str(second_evidence)]}],
        "who_should_buy": ["People who need 10 hours of battery life"],
    })
    return draft, reviews


def test_excerpt_must_match_original_speech_near_timestamp() -> None:
    body = "# Transcript\n[10.000-14.000] Battery lasted\n[14.000-18.000] 10 hours under light use.\n"
    assert transcript_excerpt_matches(body, "Battery lasted 10 hours under light use.", 10, 18)
    assert not transcript_excerpt_matches(body, "Battery lasted 10 hours under light use.", 50, 58)
    assert not transcript_excerpt_matches(body, "Battery lasted 20 hours under light use.", 10, 18)
    assert not transcript_excerpt_matches(body, "Battery lasted", None, None)


def test_long_auto_caption_quote_uses_exact_words_and_bounded_time() -> None:
    body = "\n".join(
        f"[{index * 3:.3f}-{index * 3 + 3:.3f}] {part}"
        for index, part in enumerate((
            "After weeks of testing,", "I found the battery", "could last about ten",
            "hours when browsing and", "writing, but only six", "hours during heavier work.",
        ))
    )
    quote = "After weeks of testing I found the battery could last about ten hours when browsing and writing, but only six hours during heavier work"
    assert transcript_excerpt_matches(body, quote, 0, 18)
    assert transcript_excerpt_matches(body, quote, 0, 15)
    assert not transcript_excerpt_matches(body, quote.replace("six", "eight"), 0, 18)
    assert not transcript_excerpt_matches(body, quote, 60, 78)
    assert not transcript_excerpt_matches(body, quote, 0, 60)


def test_comment_quote_matches_comment_text_not_metadata() -> None:
    body = "- [comment-1] likes=42 published=2026-01-01T12:00:00Z: The hinge broke after two months."
    assert comment_excerpt_matches(body, "The hinge broke after two months.")
    assert not comment_excerpt_matches(body, "comment-1")
    assert not comment_excerpt_matches(body, "likes=42")


def test_evidence_tool_rejects_self_quote_and_wrong_source(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id = uuid.uuid4()
    source_a, source_b, transcript, evidence = (uuid.uuid4() for _ in range(4))
    source_a_version, source_b_version, transcript_version, evidence_version = (uuid.uuid4() for _ in range(4))
    nodes = {
        source_a: SimpleNamespace(id=source_a, workspace_id=workspace_id, status="active", node_type="source", current_version_id=source_a_version),
        source_b: SimpleNamespace(id=source_b, workspace_id=workspace_id, status="active", node_type="source", current_version_id=source_b_version),
        transcript: SimpleNamespace(id=transcript, workspace_id=workspace_id, status="active", node_type="transcript", current_version_id=transcript_version),
        evidence: SimpleNamespace(id=evidence, workspace_id=workspace_id, status="active", node_type="evidence", current_version_id=evidence_version),
    }
    versions = [
        SimpleNamespace(id=evidence_version, node_id=evidence, provenance={}),
        SimpleNamespace(id=transcript_version, node_id=transcript, provenance={"video_id": "video-b"}),
        SimpleNamespace(id=source_b_version, node_id=source_b, provenance={"video_id": "video-b"}),
    ]
    db = MagicMock()
    db.get.side_effect = lambda model, key: (nodes if model.__name__ == "ContextNode" else {
        version.id: version for version in versions
    }).get(key)
    db.scalars.side_effect = [
        [SimpleNamespace(target_version_id=transcript_version)],
        [SimpleNamespace(target_version_id=source_b_version)], [], versions,
    ]
    monkeypatch.setattr(evidence_tool, "_workspace", lambda *_: SimpleNamespace(id=workspace_id))
    monkeypatch.setattr(evidence_tool, "read_version_body", lambda _workspace, version, **_kw: (
        {}, "[10.000-15.000] The real transcript says five hours."
        if version.id == transcript_version else "Battery lasted 10 hours under light use.",
    ))
    request = EvidenceValidateInput(
        evidence_node_id=evidence, source_node_id=source_a,
        evidence_text="Battery lasted 10 hours under light use.", timestamp_start_seconds=10,
    )
    wrong = evidence_tool.validate_evidence(db, object(), request)
    assert not wrong.valid and "source_lineage_missing" in wrong.error_codes
    db.scalars.side_effect = [
        [SimpleNamespace(target_version_id=transcript_version)],
        [SimpleNamespace(target_version_id=source_b_version)], [], versions,
    ]
    self_quote = evidence_tool.validate_evidence(db, object(), request.model_copy(update={"source_node_id": source_b}))
    assert not self_quote.valid and "evidence_text_or_timestamp_not_traceable" in self_quote.error_codes


def test_consensus_requires_each_named_source_and_exact_quantities() -> None:
    draft, reviews = _fixture()
    safe, audit, _ = ground_report(draft, reviews, AuditResult(verdict="pass"))
    assert audit.verdict == "pass" and safe.consensus_pros

    borrowed = draft.model_copy(update={"consensus_pros": (
        draft.consensus_pros[0].model_copy(update={"evidence_node_ids": draft.consensus_pros[0].evidence_node_ids[:1]}),
    )})
    safe, audit, terminal = ground_report(borrowed, reviews, AuditResult(verdict="pass"))
    assert not safe.consensus_pros and audit.verdict == "fail" and not terminal

    exaggerated = draft.model_copy(update={"consensus_pros": (
        draft.consensus_pros[0].model_copy(update={"statement": "Battery lasted 20 hours under light use."}),
    )})
    safe, audit, terminal = ground_report(exaggerated, reviews, AuditResult(verdict="pass"))
    assert not safe.consensus_pros and audit.verdict == "fail" and not terminal


def test_single_source_finding_can_survive_without_agreement_bonus() -> None:
    draft, reviews = _fixture()
    one_source = draft.model_copy(update={"consensus_pros": (
        draft.consensus_pros[0].model_copy(update={
            "source_ids": draft.consensus_pros[0].source_ids[:1],
            "evidence_node_ids": draft.consensus_pros[0].evidence_node_ids[:1],
        }),
    )})
    safe, audit, _ = ground_report(one_source, reviews, AuditResult(verdict="pass"))
    assert safe.consensus_pros and audit.verdict == "pass"
    third = dict(reviews[0], source_id=str(uuid.uuid4()), channel_id="channel-3")
    reviews.append(third)
    assert _score(reviews, [], 3, one_source)["confidence"] < _score(reviews, [], 3, draft)["confidence"]


def test_quantity_must_appear_in_original_excerpt() -> None:
    draft, reviews = _fixture()
    for review in reviews:
        review["claims"][0]["evidence"][0]["evidence_text"] = "Battery lasted well under light use."
    safe, audit, terminal = ground_report(draft, reviews, AuditResult(verdict="pass"))
    assert not safe.consensus_pros and audit.verdict == "fail" and not terminal


@pytest.mark.parametrize("code", ["unsupported_finding", "UNSUPPORTED_FINDING", "Unsupported_Finding"])
def test_rejected_findings_use_the_existing_correction_and_reaudit(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    draft, reviews = _fixture()
    rejected = AuditResult.model_validate({
        "verdict": "fail",
        "issues": [{"code": code, "field_path": "consensus_pros[0].statement"}],
    })
    safe, audit, terminal = ground_report(draft, reviews, rejected, strict_grounding=True)
    assert audit.verdict == "fail" and not safe.consensus_pros and not terminal
    assert any(issue.code == "grounded_conclusion_missing" for issue in audit.issues)

    run = SimpleNamespace(id=uuid.uuid4(), product_input="Test laptop", canonical_product="Test laptop",
                          requested_options={"source_count": 3})
    first_audit = {"audit": audit.model_dump(mode="json"), "grounding_terminal": terminal}
    corrected = {"draft": draft.model_dump(mode="json"), "source_analyses": reviews}
    outputs = {"audit_report": first_audit, "build_consensus": corrected,
               "correct_consensus": corrected, "discover_candidates": {}}
    monkeypatch.setattr(executor, "_task_output", lambda _run_id, key: outputs.get(key))
    monkeypatch.setattr(executor, "_outputs_with_prefix", lambda *_args: [{"analysis": review} for review in reviews])
    correction, _ = executor._agent_task_input(
        AGENT_REGISTRY["consensus_analyst"], SimpleNamespace(input_payload={"correction_stage": True}), run,
    )
    assert "_shortcut" not in correction
    assert correction["source_analyses"] == reviews
    assert correction["report_under_repair"] == corrected["draft"]
    assert {issue["code"] for issue in correction["correction_issues"]} >= {
        "unsupported_finding", "grounded_conclusion_missing",
    }
    reaudit, _ = executor._agent_task_input(
        AGENT_REGISTRY["quality_auditor"], SimpleNamespace(input_payload={"reaudit_stage": True}), run,
    )
    assert "_shortcut" not in reaudit and reaudit["report_draft"] == corrected["draft"]


def test_passing_first_audit_skips_correction_and_reaudit(monkeypatch: pytest.MonkeyPatch) -> None:
    draft, reviews = _fixture()
    safe, audit, terminal = ground_report(draft, reviews, AuditResult(verdict="pass"))
    assert audit.verdict == "pass" and not terminal
    run = SimpleNamespace(id=uuid.uuid4())
    original = {"draft": draft.model_dump(mode="json")}
    first_audit = {"audit": audit.model_dump(mode="json"), "safe_draft": safe.model_dump(mode="json")}
    monkeypatch.setattr(executor, "_task_output", lambda _run_id, key: {
        "audit_report": first_audit, "build_consensus": original,
    }.get(key))
    monkeypatch.setattr(executor, "_outputs_with_prefix", lambda *_args: [])
    correction, _ = executor._agent_task_input(
        AGENT_REGISTRY["consensus_analyst"], SimpleNamespace(input_payload={"correction_stage": True}), run,
    )
    reaudit, _ = executor._agent_task_input(
        AGENT_REGISTRY["quality_auditor"], SimpleNamespace(input_payload={"reaudit_stage": True}), run,
    )
    assert correction["_shortcut"] == original
    assert reaudit["_shortcut"] == first_audit


def test_missing_central_evidence_remains_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    draft, reviews = _fixture()
    for review in reviews:
        review["claims"][0]["central"] = False
    safe, audit, terminal = ground_report(draft, reviews, AuditResult(verdict="pass"))
    assert audit.verdict == "fail" and terminal and safe.consensus_pros
    run = SimpleNamespace(id=uuid.uuid4())
    original = {"draft": draft.model_dump(mode="json")}
    first_audit = {"audit": audit.model_dump(mode="json"), "grounding_terminal": terminal}
    monkeypatch.setattr(executor, "_task_output", lambda _run_id, key: {
        "audit_report": first_audit, "build_consensus": original,
    }.get(key))
    monkeypatch.setattr(executor, "_outputs_with_prefix", lambda *_args: [])
    correction, _ = executor._agent_task_input(
        AGENT_REGISTRY["consensus_analyst"], SimpleNamespace(input_payload={"correction_stage": True}), run,
    )
    assert correction["_shortcut"] == original


def test_failed_reaudit_never_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_task_output", lambda _run_id, key: {
        "reaudit_report": {"audit": {"verdict": "fail", "issues": [{
            "code": "grounded_conclusion_missing", "field_path": "report_draft",
        }]}}
    }.get(key))
    with pytest.raises(RuntimeTaskError, match="report_audit_failed"):
        executor._publish_report(
            uuid.uuid4(), SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(), config=SimpleNamespace(),
        )


def test_auditor_can_remove_unsupported_narrative_without_another_call() -> None:
    draft, reviews = _fixture()
    model_audit = AuditResult.model_validate({
        "verdict": "fail",
        "issues": [{"code": "unsupported_narrative", "field_path": "report_draft.summary", "retryable": True}],
    })
    safe, audit, terminal = ground_report(draft, reviews, model_audit)
    assert audit.verdict == "pass_with_warnings" and not terminal
    assert safe.summary.startswith("This report analyzed 2 cited reviews.")


def test_benign_audit_warning_keeps_grounded_report() -> None:
    draft, reviews = _fixture()
    warning = AuditResult.model_validate({
        "verdict": "pass_with_warnings",
        "issues": [{"code": "translated_caption_warning", "field_path": "source_analyses", "retryable": False}],
    })
    safe, audit, terminal = ground_report(draft, reviews, warning)
    assert safe.consensus_pros and audit.verdict == "pass_with_warnings" and not terminal


def test_new_grounding_audit_failure_does_not_retry_model() -> None:
    draft, reviews = _fixture()
    issue = AuditResult.model_validate({
        "verdict": "fail",
        "issues": [{"code": "unexpected_grounding_code", "field_path": "report_draft.summary", "retryable": True}],
    })
    _, audit, terminal = ground_report(draft, reviews, issue, strict_grounding=True)
    assert audit.verdict == "fail" and terminal


def test_confidence_uses_cited_agreement_and_disagreement() -> None:
    draft, reviews = _fixture()
    third = dict(reviews[0])
    third.update(source_id=str(uuid.uuid4()), channel_id="channel-3")
    reviews.append(third)
    agreed = _score(reviews, [], 3, draft)
    data = draft.model_dump(mode="json")
    data["disagreements"] = [
        {"topic": "battery", "side_a": "long", "side_a_source_ids": [reviews[0]["source_id"]],
         "side_b": "short", "side_b_source_ids": [reviews[1]["source_id"]]},
    ]
    disagreed = _score(reviews, [], 3, FinalReportDraft.model_validate(data))
    assert disagreed["confidence"] < agreed["confidence"]


def test_curated_order_prefers_independent_complementary_sources() -> None:
    candidates = {
        "a": {"channel_id": "one", "title": "Review", "deterministic_score": 0.9},
        "b": {"channel_id": "one", "title": "Review part two", "deterministic_score": 0.88},
        "c": {"channel_id": "two", "title": "Long-term review", "deterministic_score": 0.78},
    }
    order = balance_curated_order(["a", "b", "c"], candidates, {"c": "long_term"}, 2)
    assert set(order[:2]) == {"a", "c"} and len(order) == 3


def test_decision_guide_marks_unestablished_purchase_checks() -> None:
    validated = SimpleNamespace(
        product_canonical_name="Test headphones", product_info=None,
        longest_usage_period=None, longest_usage_source_id=None,
    )
    guide = _decision_guide(
        validated, [{"id": "source-1", "claims": [{"claim": "Sound is clear"}]}],
        [{"id": "finding-1"}], [], {},
    )
    assert guide["buy_if_finding_ids"] == ["finding-1"]
    assert "Current warranty terms" in guide["unknowns"]
    assert "Exact configuration reviewers tested" in guide["unknowns"]
    assert "Long-session comfort" in guide["unknowns"]


def test_default_run_system_prompt_budget_does_not_increase() -> None:
    # Baseline before the buyer-grounding prompts: 2,515 estimated tokens.
    calls = {
        "research_coordinator": 1, "source_curator": 1, "review_analyst": 5,
        "knowledge_curator": 0,
        "consensus_analyst": 1, "quality_auditor": 1,
    }
    total = sum(
        estimate_tokens(AGENT_REGISTRY[key].persisted_payload()["system_prompt"]) * count
        for key, count in calls.items()
    )
    assert total <= 2515


@pytest.mark.skipif(not _BUDGET_SCRIPT.is_file(), reason="host-only staging script is outside the backend test image")
def test_staging_budget_gate_rejects_token_or_latency_regression() -> None:
    compare = runpy.run_path(str(_BUDGET_SCRIPT))["compare"]
    baseline = {str(index): {
        "total_tokens": 100, "model_call_count": 8, "completion_ms": 1000,
        "quote_valid_rate": .9, "unsupported_claim_rate": .1, "buyer_coverage_rate": .5,
    } for index in range(20)}
    candidate = {key: {**value, "total_tokens": 95, "completion_ms": 950,
                       "quote_valid_rate": 1, "unsupported_claim_rate": 0,
                       "buyer_coverage_rate": .7} for key, value in baseline.items()}
    assert compare(baseline, candidate)["passed"]
    candidate["0"]["completion_ms"] = 1100
    candidate["1"]["completion_ms"] = 1100
    assert not compare(baseline, candidate)["passed"]


@pytest.mark.skipif(not _BUDGET_SCRIPT.is_file(), reason="host-only staging script is outside the backend test image")
def test_staging_gate_allows_only_two_recorded_repair_calls() -> None:
    module = runpy.run_path(str(_BUDGET_SCRIPT))
    compare = module["compare"]
    repair_calls = module["repair_calls"]
    baseline = {str(index): {
        "total_tokens": 100, "model_call_count": 8, "completion_ms": 1000,
        "quote_valid_rate": .9, "unsupported_claim_rate": .1, "buyer_coverage_rate": .5,
    } for index in range(20)}
    candidate = {key: {**value} for key, value in baseline.items()}
    candidate["0"].update({
        "first_audit_repairable": True, "correction_model_calls": 1, "reaudit_model_calls": 1,
        "model_call_count": 10, "total_tokens": 140, "completion_ms": 1300,
    })
    assert compare(baseline, candidate)["passed"]
    assert compare(baseline, candidate)["repair_cases"] == 1
    candidate["0"]["model_call_count"] = 11
    assert not compare(baseline, candidate)["checks"]["model_calls"]
    candidate["0"]["model_call_count"] = 10
    candidate["0"]["unsupported_claim_rate"] = 1
    assert not compare(baseline, candidate)["checks"]["unsupported_claims"]
    with pytest.raises(ValueError, match="repair calls require"):
        repair_calls({"correction_model_calls": 1})
    with pytest.raises(ValueError, match="reaudit requires"):
        repair_calls({"first_audit_repairable": True, "reaudit_model_calls": 1})


@pytest.mark.skipif(not _BUDGET_SCRIPT.is_file(), reason="host-only staging script is outside the backend test image")
def test_strict_coverage_gate_counts_repairs_and_rejects_regressions() -> None:
    compare = runpy.run_path(str(_BUDGET_SCRIPT))["compare"]
    baseline = {str(index): {
        "total_tokens": 100, "model_call_count": 11, "completion_ms": 1000,
        "quote_valid_rate": 1, "unsupported_claim_rate": 0, "buyer_coverage_rate": 1,
        "source_count_requested": 5, "source_count_analyzed": 2,
    } for index in range(20)}
    candidate = {key: {**value, "total_tokens": 90, "model_call_count": 10,
        "completion_ms": 900, "source_count_analyzed": 5} for key, value in baseline.items()}
    assert compare(baseline, candidate, strict_coverage=True)["passed"]
    with pytest.raises(ValueError, match="at least 20"):
        compare({"0": baseline["0"]}, {"0": candidate["0"]}, strict_coverage=True)
    for key in ("0", "1"):
        candidate[key].update(first_audit_repairable=True, correction_model_calls=1,
                              reaudit_model_calls=1, model_call_count=12,
                              total_tokens=300, completion_ms=1500)
    checks = compare(baseline, candidate, strict_coverage=True)["checks"]
    assert checks["model_calls"] and not checks["mean_tokens"] and not checks["p95_latency"]
    candidate = {key: {**value} for key, value in baseline.items()}
    assert not compare(baseline, candidate, strict_coverage=True)["checks"]["source_coverage"]


@pytest.mark.skipif(not _BUDGET_SCRIPT.is_file(), reason="host-only staging script is outside the backend test image")
def test_strict_correction_gate_includes_repairs_and_requires_fact_retention() -> None:
    compare = runpy.run_path(str(_BUDGET_SCRIPT))["compare"]
    baseline = {str(index): {
        "total_tokens": 100, "model_call_count": 11, "completion_ms": 1000,
        "quote_valid_rate": 1, "unsupported_claim_rate": 0, "buyer_coverage_rate": 1,
        "source_count_requested": 5, "source_count_analyzed": 5, "product_fact_count": 0,
    } for index in range(20)}
    candidate = {key: {**value, "total_tokens": 90, "model_call_count": 9,
                       "completion_ms": 900, "product_fact_count": 4} for key, value in baseline.items()}
    assert compare(baseline, candidate, strict_correction=True)["passed"]
    with pytest.raises(ValueError, match="at least 20"):
        compare({"0": baseline["0"]}, {"0": candidate["0"]}, strict_correction=True)
    for key in ("0", "1"):
        candidate[key].update(first_audit_repairable=True, correction_model_calls=1,
                              reaudit_model_calls=1, model_call_count=11,
                              total_tokens=300, completion_ms=1500)
    checks = compare(baseline, candidate, strict_correction=True)["checks"]
    assert checks["normal_calls"] and not checks["mean_tokens"] and not checks["p95_latency"]
    candidate = {key: {**value, "model_call_count": 9} for key, value in baseline.items()}
    assert not compare(baseline, candidate, strict_correction=True)["checks"]["product_facts"]
    candidate["0"]["model_call_count"] = 10
    assert not compare(baseline, candidate, strict_correction=True)["checks"]["normal_calls"]
