"""Captured audit regressions; fixture decisions are not live-model verification."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.admin.evaluation import _checks
from app.analysis.audit import AuditDecisionError, DecisionAuditorInput, FindingAuditResult, decision_audit_input, finding_audit_schema
from app.analysis.contracts import AuditResult, FinalReportDraft
from app.analysis.grounding import ground_report
from app.analysis.registry import snapshot_input_model, snapshot_output_model
from app.analysis.synthesis import (
    EvidenceBoundBuyingSynthesis, RepairSynthesisInput, SourceBoundBuyingSynthesis,
    SynthesisBindingError, evidence_bound_synthesis_schema, evidence_catalog, repair_synthesis_input, unchanged_rejected_findings,
)
from app.llmops.contracts import strictify_json_schema


def captured():
    return json.loads((Path(__file__).parent / "fixtures/iphone_audit_rejection.json").read_text(encoding="utf-8-sig"))


def audit_input(fixture):
    return DecisionAuditorInput.model_validate(decision_audit_input(
        {"report_draft": fixture["draft"], "source_analyses": fixture["source_analyses"]}))


def supported_decisions(supplied):
    return {"finding_checks": [{"field_path": finding.field_path, "supported": True,
        "evidence_refs": [q.evidence_ref for q in finding.citations], "category": None,
        "unsupported_clause": None, "explanation": None}
        for finding in (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons)],
        "other_issues": []}


def test_captured_noop_correction_is_rejected_even_when_duration_changes():
    fixture = captured()
    unchanged = unchanged_rejected_findings(fixture["draft"],
        FinalReportDraft.model_validate(fixture["correction_draft"]), fixture["audit"]["issues"])
    assert len(unchanged) == 12
    draft = copy.deepcopy(fixture["correction_draft"])
    draft["consensus_pros"] = []
    draft["consensus_cons"] = []
    assert not unchanged_rejected_findings(fixture["draft"], FinalReportDraft.model_validate(draft), fixture["audit"]["issues"])


def test_new_synthesis_derives_one_owner_and_preserves_legacy():
    fixture = captured()
    _, bindings, _ = evidence_catalog(fixture["source_analyses"])
    refs = list(bindings)
    payload = {"summary": "The reviewer describes battery life.", "assertions": [
        {"kind": "strength", "attribute": "Battery", "observation": "The reviewer describes battery life.",
         "evidence_refs": refs[:1]}]}
    model = EvidenceBoundBuyingSynthesis.model_validate(payload)
    report = model.as_report("iPhone", "iPhone 17 Pro Max", fixture["source_analyses"])
    assert [str(s) for s in report.consensus_pros[0].source_ids] == [bindings[refs[0]][0]]
    foreign = next(ref for ref in refs if bindings[ref][0] != bindings[refs[0]][0])
    payload["assertions"][0]["evidence_refs"].append(foreign)
    with pytest.raises(SynthesisBindingError, match="assertion_source_mismatch"):
        EvidenceBoundBuyingSynthesis.model_validate(payload).as_report("iPhone", "iPhone", fixture["source_analyses"])
    payload["assertions"][0]["evidence_refs"] = ["e999"]
    with pytest.raises(SynthesisBindingError, match="unknown_reference"):
        EvidenceBoundBuyingSynthesis.model_validate(payload).as_report("iPhone", "iPhone", fixture["source_analyses"])
    assert snapshot_output_model("consensus_analyst", SourceBoundBuyingSynthesis.model_json_schema()) is SourceBoundBuyingSynthesis
    assert snapshot_output_model("quality_auditor", AuditResult.model_json_schema()) is AuditResult
    assert snapshot_input_model("quality_auditor", DecisionAuditorInput.model_json_schema()) is DecisionAuditorInput


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_path", "foreign", "span"])
def test_audit_decisions_reject_incomplete_or_unbound_checks(mutation):
    supplied = audit_input(captured())
    response = supported_decisions(supplied)
    first = response["finding_checks"][0]
    if mutation == "missing":
        response["finding_checks"].pop()
    elif mutation == "duplicate":
        response["finding_checks"].append(copy.deepcopy(first))
    elif mutation == "wrong_path":
        first["field_path"] = "report_draft.consensus_pros[99]"
    elif mutation == "foreign":
        first["evidence_refs"] = [response["finding_checks"][3]["evidence_refs"][0]]
    else:
        first.update(supported=False, category="condition", unsupported_clause="invented span", explanation="Missing condition.")
    with pytest.raises(ValueError):
        FindingAuditResult.model_validate(response).as_audit(supplied)


def test_rejection_requires_reason_and_supplies_precise_repair_target():
    fixture = captured()
    supplied = audit_input(fixture)
    response = supported_decisions(supplied)
    rejection = response["finding_checks"][1]
    rejection.update(supported=False, category="condition", unsupported_clause="after over 6 months without a screen protector",
                     explanation="The cited quotes do not establish this duration or screen-protector condition.")
    invalid = copy.deepcopy(response)
    invalid["finding_checks"][1]["explanation"] = None
    with pytest.raises(ValidationError):
        FindingAuditResult.model_validate(invalid)
    result, diagnostics = FindingAuditResult.model_validate(response).as_audit(supplied)
    assert result.verdict == "fail" and len(result.issues) == 1
    repair = RepairSynthesisInput.model_validate(repair_synthesis_input({
        "product_display_name": "iPhone", "product_canonical_name": "iPhone 17 Pro Max", "requested_source_count": 5,
        "source_analyses": fixture["source_analyses"], "report_under_repair": fixture["draft"],
        "correction_issues": result.model_dump(mode="json")["issues"], "audit_diagnostics": diagnostics}))
    assert repair.repair_targets[0].unsupported_clause == rejection["unsupported_clause"]
    assert repair.repair_targets[0].evidence_refs
    assert "confidence" not in repair.model_dump_json()
    safe, audit, _ = ground_report(FinalReportDraft.model_validate(fixture["draft"]), fixture["source_analyses"], result)
    assert len(safe.consensus_pros) + len(safe.consensus_cons) == 11
    assert audit.verdict == "pass_with_warnings"


@pytest.mark.parametrize("category,clause,explanation", [
    ("quantity", "6 hours", "The supplied excerpt contains a different quantity."),
    ("condition", "from a drop", "The excerpt reports a dent without stating its cause."),
    ("polarity", "superb", "The changed excerpt describes poor rather than superb battery life."),
])
def test_explained_negative_checks_keep_semantic_defects_blocked(category, clause, explanation):
    fixture = captured()
    supplied = audit_input(fixture)
    response = supported_decisions(supplied)
    finding = next(item for item in response["finding_checks"]
                   if clause in next(f.statement for f in (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons)
                                     if f.field_path == item["field_path"]))
    finding.update(supported=False, category=category, unsupported_clause=clause, explanation=explanation)
    audit, diagnostic = FindingAuditResult.model_validate(response).as_audit(supplied)
    assert audit.verdict == "fail" and diagnostic["rejections"][0]["explanation"] == explanation


def test_evaluation_cannot_pass_an_auditor_that_rejects_every_supported_finding():
    checks = _checks("quality_auditor", {"verdict": "fail", "issues": [
        {"code": "unsupported_narrative", "field_path": "report_draft.summary"},
        {"code": "unsupported_finding", "field_path": "report_draft.consensus_pros[0]"}]})
    assert checks["unsupported_claim_rejected"] and not checks["supported_finding_preserved"]


def test_narrative_rejection_rejects_blank_reason_and_foreign_side_evidence():
    fixture = captured()
    supplied = audit_input(fixture)
    response = supported_decisions(supplied)
    response["other_issues"] = [{"code": "unsupported_narrative", "field_path": "report_draft.summary",
        "evidence_refs": [], "unsupported_clause": "Battery life", "explanation": "   "}]
    with pytest.raises(ValidationError):
        FindingAuditResult.model_validate(response)
    response["other_issues"][0].update(explanation="Unsupported narrative.", evidence_refs=["e999"])
    with pytest.raises(ValueError, match="unknown evidence"):
        FindingAuditResult.model_validate(response).as_audit(supplied)


def test_live_generation_schema_requires_exact_paths_and_consistent_decisions():
    supplied = audit_input(captured())
    schema = strictify_json_schema(finding_audit_schema(supplied))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    response = supported_decisions(supplied)
    assert not list(validator.iter_errors(response))
    response["finding_checks"][0]["field_path"] = "consensus_pros[0]"
    assert list(validator.iter_errors(response))
    response = supported_decisions(supplied)
    response["finding_checks"][0]["explanation"] = "This quote supports the finding."
    assert list(validator.iter_errors(response))
    response = supported_decisions(supplied)
    response["finding_checks"][0]["supported"] = False
    assert list(validator.iter_errors(response))
    response["finding_checks"].pop()
    assert list(validator.iter_errors(response))


def test_incomplete_decisions_expose_bounded_structural_diagnostics():
    supplied = audit_input(captured())
    response = supported_decisions(supplied)
    response["finding_checks"][0]["field_path"] = "not-an-original-path"
    with pytest.raises(AuditDecisionError) as caught:
        FindingAuditResult.model_validate(response).as_audit(supplied)
    assert caught.value.diagnostics == {"missing_paths": ["report_draft.consensus_pros[0]"],
        "unknown_path_count": 1, "duplicate_path_count": 0}


@pytest.mark.parametrize("duplicate", [False, True])
def test_narrative_retry_identifies_the_exact_field_and_reason(duplicate):
    supplied = audit_input(captured())
    response = supported_decisions(supplied)
    issue = {"code": "unsupported_narrative", "field_path": "report_draft.summary", "evidence_refs": [],
        "unsupported_clause": supplied.report_draft.summary[:20] if duplicate else "An invented missing clause",
        "explanation": "This clause lacks support."}
    response["other_issues"] = [issue, copy.deepcopy(issue)] if duplicate else [issue]
    with pytest.raises(AuditDecisionError) as caught:
        FindingAuditResult.model_validate(response).as_audit(supplied)
    assert caught.value.diagnostics["issues"] == [{
        "loc": ["other_issues", 1 if duplicate else 0, "field_path" if duplicate else "unsupported_clause"],
        "field_path": "report_draft.summary",
        "type": "duplicate_narrative_rejection" if duplicate else "invalid_rejection_span"}]


def test_live_audit_schema_rejects_unavailable_or_mismatched_narrative_paths():
    supplied = audit_input(captured())
    validator = Draft202012Validator(strictify_json_schema(finding_audit_schema(supplied)))
    response = supported_decisions(supplied)
    issue = {"code": "unsupported_narrative", "field_path": "report_draft.summary", "evidence_refs": [],
             "unsupported_clause": supplied.report_draft.summary[:20], "explanation": "The clause lacks support."}
    response["other_issues"] = [issue]
    assert not list(validator.iter_errors(response))
    for path in ("summary", "report_draft.limitations[0]", "report_draft.who_should_buy[99]"):
        issue["field_path"] = path
        assert list(validator.iter_errors(response))
    issue.update(field_path="report_draft.summary", code="unsupported_disagreement")
    assert list(validator.iter_errors(response))


def test_live_synthesis_schema_rejects_mixed_and_unknown_citation_owners():
    fixture = captured()
    supplied = RepairSynthesisInput.model_validate(repair_synthesis_input({
        "product_display_name": "iPhone", "product_canonical_name": "iPhone 17 Pro Max", "requested_source_count": 5,
        "source_analyses": fixture["source_analyses"]}))
    schema = strictify_json_schema(evidence_bound_synthesis_schema(supplied))
    Draft202012Validator.check_schema(schema)
    refs = [q.evidence_ref for q in supplied.evidence_catalog]
    first_owner = supplied.evidence_catalog[0].source_ref
    foreign = next(q.evidence_ref for q in supplied.evidence_catalog if q.source_ref != first_owner)
    payload = {"summary": "Cited observations.", "assertions": [{"kind": "strength", "attribute": "Battery",
        "observation": "The reviewer reports superb battery life.", "conditions": None, "evidence_refs": refs[:1]}],
        "disagreements": [], "longest_usage_period": None, "longest_usage_source_ref": None,
        "who_should_buy": [], "who_should_avoid": [], "limitations": []}
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(payload))
    payload["assertions"][0]["evidence_refs"].append(foreign)
    assert list(validator.iter_errors(payload))
    payload["assertions"][0]["evidence_refs"] = ["e999"]
    assert list(validator.iter_errors(payload))
