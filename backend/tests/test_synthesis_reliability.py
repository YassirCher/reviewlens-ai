"""Captured evidence and synthetic formatting faults; these do not assert live accuracy."""
import copy
import json
from pathlib import Path

import pytest

from app.analysis.contracts import AuditResult, FinalReportDraft, SourceAnalysisDraft
from app.analysis.audit import DecisionAuditorInput, FindingAuditResult, decision_audit_input
from app.analysis.grounding import _statement_matches, ground_report
from app.analysis.product_info import ProductEvidence, ProductFact, merge_product_info
from app.analysis.registry import AGENT_REGISTRY, evaluate_agent_spec, snapshot_input_model, snapshot_output_model
from app.analysis.rendering import DistinctBuyingSynthesis, NormalizedBuyingSynthesis, PrioritizedSynthesisInput, prioritized_synthesis_input
from app.analysis.review import ClassifiedVideoExtraction, VideoExtraction, normalize_usage
from app.analysis.synthesis import CatalogRepairSynthesisInput, EvidenceBoundBuyingSynthesis, SynthesisBindingError, evidence_catalog


def captured():
    return json.loads((Path(__file__).parent / "fixtures/comments_synthesis_failure.json").read_text(encoding="utf-8-sig"))


def response(fixture):
    quotes, _, _ = evidence_catalog(fixture["source_analyses"])
    return {"summary": "Cited reviewer observations.", "assertions": [
        {"kind": "strength", "attribute": "Reviewer observation", "observation": q["excerpt"][:150],
         "conditions": None, "evidence_refs": [q["evidence_ref"]]} for q in quotes["evidence_catalog"][:2]]}


@pytest.mark.parametrize("field,text", [("observation", "s1 reports lasting all day"),
    ("conditions", "(e1)"), ("attribute", "s1 battery"), ("observation", "A cited observation [e1]")])
def test_owned_prose_aliases_render_without_retry(field, text):
    fixture = captured(); payload = response(fixture)
    payload["assertions"][0][field] = text
    draft, diagnostic = NormalizedBuyingSynthesis.model_validate(payload).compile_report("S25 Ultra", "S25 Ultra", fixture["source_analyses"])
    assert len(draft.consensus_pros) == 2
    assert "s1" not in draft.consensus_pros[0].statement and "e1" not in draft.consensus_pros[0].statement
    assert any(d["action"] == "normalized" for d in diagnostic)


@pytest.mark.parametrize("alias", ["s2", "s99", "e9999"])
def test_foreign_or_unresolved_prose_omits_only_its_assertion(alias):
    fixture = captured(); payload = response(fixture)
    payload["assertions"][0]["observation"] = alias + " reports lasting all day"
    draft, diagnostic = NormalizedBuyingSynthesis.model_validate(payload).compile_report("S25 Ultra", "S25 Ultra", fixture["source_analyses"])
    assert len(draft.consensus_pros) == 1
    assert diagnostic[0]["loc"] == ["assertions", 0, "observation"]
    assert diagnostic[0]["action"] == "omitted"


@pytest.mark.parametrize("code", ["E1", "S1", "S25"])
def test_genuine_requested_product_codes_survive(code):
    fixture = captured(); payload = response(fixture)
    payload["assertions"][0]["observation"] = f"The reviewer discusses {code} battery life"
    assert len(NormalizedBuyingSynthesis.model_validate(payload).as_report(f"Example {code}", f"Example {code}", fixture["source_analyses"]).consensus_pros) == 2


def test_unknown_and_mixed_citation_ownership_still_fail():
    fixture = captured(); payload = response(fixture)
    payload["assertions"][0]["evidence_refs"] = ["e999"]
    with pytest.raises(SynthesisBindingError, match="unknown_reference"):
        NormalizedBuyingSynthesis.model_validate(payload).as_report("Phone", "Phone", fixture["source_analyses"])
    _, bindings, _ = evidence_catalog(fixture["source_analyses"])
    payload = response(fixture)
    foreign = next(ref for ref, (owner, _) in bindings.items() if owner != bindings["e1"][0])
    payload["assertions"][0]["evidence_refs"].append(foreign)
    with pytest.raises(SynthesisBindingError, match="assertion_source_mismatch"):
        NormalizedBuyingSynthesis.model_validate(payload).as_report("Phone", "Phone", fixture["source_analyses"])


@pytest.mark.parametrize("placeholder", ["e1", "the cited excerpt"])
def test_reference_only_observations_and_disagreements_do_not_become_placeholders(placeholder):
    fixture = captured(); payload = response(fixture)
    catalog, bindings, _ = evidence_catalog(fixture["source_analyses"])
    foreign = next(ref for ref, (owner, _) in bindings.items() if owner != bindings["e1"][0])
    payload["assertions"][0]["observation"] = placeholder
    payload["disagreements"] = [{"topic": "Battery", "side_a": placeholder, "side_a_evidence_refs": ["e1"],
        "side_b": foreign, "side_b_evidence_refs": [foreign]}]
    draft, diagnostics = NormalizedBuyingSynthesis.model_validate(payload).compile_report("Phone", "Phone", fixture["source_analyses"])
    assert len(draft.consensus_pros) == 1 and not draft.disagreements
    assert any(d["loc"] == ["assertions", 0, "observation"] and d["type"] == "reference_only_prose" and d["ownership"] == "owned" for d in diagnostics)
    assert any(d["loc"] == ["disagreements", 0, "side_a"] and d["type"] == "reference_only_prose" for d in diagnostics)


def test_all_omissions_reach_the_bounded_repair_gate_without_publishing():
    fixture = captured(); payload = response(fixture)
    for item in payload["assertions"]: item["observation"] = "s99 reports a benefit"
    draft = NormalizedBuyingSynthesis.model_validate(payload).as_report("Phone", "Phone", fixture["source_analyses"])
    _, audit, terminal = ground_report(draft, fixture["source_analyses"], AuditResult(verdict="pass"), strict_grounding=True)
    assert audit.verdict == "fail" and not terminal
    reviews = copy.deepcopy(fixture["source_analyses"])
    for r in reviews:
        for claim in r["claims"]: claim["central"] = False
    assert ground_report(draft, reviews, AuditResult(verdict="pass"), strict_grounding=True)[2]


@pytest.mark.parametrize("statement,quote,expected", [
    ("Default f/1.8 aperture", "Default f1.8 aperture", True),
    ("Default f1.8 aperture", "Default f/1.8 aperture", True),
    ("Default f/1.8 aperture", "Default f/2.8 aperture", False),
    ("Default f/1.8 aperture", "Weight is 1.8 g", False),
    ("Capacity is 500 mAh", "Weight is 500 g", False),
    ("Capacity is 500 mAh", "Capacity is 0.5 Ah", True),
    ("Weight is 18 g", "Weight is 249 g", False),
    ("Weight is 3.2 g", "Weight is 0.0032 kg", True),
    ("Weight is 3.2 g", "Weight is 3.2 kg", False),
    ("Latency is 100 ms", "Latency is 0.1 seconds", True),
    ("Latency is 100 ms", "Capacity is 100 mAh", False),
    ("Latency is 100 milliseconds", "Latency is 100 microseconds", False),
    ("Battery lasted 19.5 hours, versus last year's 18 hours", "Battery lasted 19 1/2 hours", False),
    ("Battery lasted 19.5 hours, versus last year's 18 hours", "Battery lasted 19 1/2 hours. Last year it lasted 18 hours", True),
])
def test_exact_dimension_specific_evidence(statement, quote, expected):
    assert _statement_matches(statement, "", quote, "iPhone 18 Pro Max") is expected


def test_drawback_capacity_uses_owned_quotes_and_stays_within_twelve():
    fixture = captured(); payload = response(fixture)
    payload["assertions"] = [copy.deepcopy(payload["assertions"][0]) for _ in range(12)]
    quotes, bindings, _ = evidence_catalog(fixture["source_analyses"])
    quote = quotes["evidence_catalog"][-1]
    owner, eid = bindings[quote["evidence_ref"]]
    metadata = [{"kind": "caveat", "topic": "Battery limitation", "source_id": owner, "central": True,
                 "evidence_node_ids": [eid]}]
    draft, diagnostic = NormalizedBuyingSynthesis.model_validate(payload).compile_report("Phone", "Phone", fixture["source_analyses"], metadata)
    assert len(draft.consensus_pros) == 11 and len(draft.consensus_cons) == 1
    assert quote["excerpt"] in draft.consensus_cons[0].statement
    assert str(draft.consensus_cons[0].source_ids[0]) == owner
    assert any(item["type"] == "drawback_quote_reserved" for item in diagnostic)
    supplied = prioritized_synthesis_input({"product_display_name": "Phone", "product_canonical_name": "Phone",
        "requested_source_count": 5, "source_analyses": fixture["source_analyses"], "claim_catalog": metadata})
    priority = PrioritizedSynthesisInput.model_validate(supplied).drawback_priorities[0]
    assert priority.topic == "Battery limitation" and priority.evidence_ref == quote["evidence_ref"]


def test_processor_conflicts_keep_scopes_components_and_citations_separate():
    ref = ProductEvidence(video_id="abc123DEF45", source_url="https://www.youtube.com/watch?v=abc123DEF45",
                          source_part="description", excerpt="A cited processor model")
    facts = [ProductFact(group="chipset", label="chipset_name", value="Apple A20 Pro", evidence=(ref, ref)),
             ProductFact(group="chip", label="Processor", value="A16 Pro", evidence=(ref,)),
             ProductFact(group="GPU", label="Processor", value="Different component", evidence=(ref,)),
             ProductFact(group="chip", label="Processor", value="Other variant", scope="Variant B", evidence=(ref,))]
    merged = merge_product_info([{"facts": [f.model_dump(mode="json") for f in facts]}])
    assert [f.conflicting for f in merged.facts] == [True, True, False, False]
    assert all(len(f.evidence) == 1 for f in merged.facts)


def test_captured_drawback_replaces_the_same_quotation_in_strengths():
    """Human-labelled placement regression; supplied metadata is not model accuracy."""
    fixture = json.loads((Path(__file__).parent / "fixtures/live_finding_kind.json").read_text(encoding="utf-8"))
    reviews = fixture["source_analyses"]
    original = FinalReportDraft.model_validate(fixture["draft"])
    catalog, bindings, _ = evidence_catalog(reviews)
    refs = {eid: ref for ref, (_, eid) in bindings.items()}
    wrong = original.consensus_pros[3]
    quoted_id = str(wrong.evidence_node_ids[0])
    quoted_text = next(q["excerpt"] for q in catalog["evidence_catalog"] if q["evidence_ref"] == refs[quoted_id])
    assert "dying pretty quickly" in quoted_text
    assert "strength" in {item["kind"] for item in fixture["original_claim_catalog"] if quoted_id in item["evidence_node_ids"]}
    payload = {"summary": "Cited owner observations.", "assertions": [
        {"kind": "strength", "attribute": "Battery", "observation": quoted_text,
         "evidence_refs": [refs[quoted_id]]},
        {"kind": "strength", "attribute": "Battery", "observation": original.consensus_pros[0].statement,
         "evidence_refs": [refs[str(original.consensus_pros[0].evidence_node_ids[0])]]}]}
    # The fixture's independent human label corrects the original model kind.
    labelled = [{"kind": "caveat", "topic": "Battery drain", "central": True,
                 "source_id": str(wrong.source_ids[0]), "evidence_node_ids": [quoted_id]}]
    draft, _ = NormalizedBuyingSynthesis.model_validate(payload).compile_report(
        original.product_display_name, original.product_canonical_name, reviews, labelled)
    assert len(draft.consensus_pros) == len(draft.consensus_cons) == 1
    assert all(quoted_id not in {str(eid) for eid in pro.evidence_node_ids} for pro in draft.consensus_pros)
    assert quoted_text in draft.consensus_cons[0].statement


def test_human_labelled_negative_quote_is_rejected_as_a_strength():
    """Verify rejection plumbing, with human decisions explicitly separate from live output."""
    fixture = json.loads((Path(__file__).parent / "fixtures/live_finding_kind.json").read_text(encoding="utf-8"))
    supplied = DecisionAuditorInput.model_validate(decision_audit_input({
        "report_draft": fixture["draft"], "source_analyses": fixture["source_analyses"]}))
    negative_paths = set(fixture["expected_rejected_pro_paths"])
    decisions = []
    for finding in (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons):
        negative = finding.field_path in negative_paths
        decisions.append({"field_path": finding.field_path, "supported": not negative,
            "evidence_refs": [q.evidence_ref for q in finding.citations],
            "category": "polarity" if negative else None,
            "unsupported_clause": finding.statement if negative else None,
            "explanation": "This battery-drain observation is a drawback, not a benefit." if negative else None})
    audit, diagnostics = FindingAuditResult.model_validate({"finding_checks": decisions, "other_issues": []}).as_audit(supplied)
    assert {item["field_path"] for item in diagnostics["rejections"]} == negative_paths
    safe, result, terminal = ground_report(FinalReportDraft.model_validate(fixture["draft"]),
                                          fixture["source_analyses"], audit, strict_grounding=True)
    assert result.verdict == "pass_with_warnings" and not terminal
    assert len(safe.consensus_pros) == len(supplied.report_draft.consensus_pros) - len(negative_paths)


@pytest.mark.parametrize("raw,transcript,expected_type,days", [
    ("a full day", "I have been using it for a full day", "short_term", 1),
    ("six months", "I have owned it for six months", "long_term", 180),
    ("six months", "The battery lasts a full day", "unknown", None),
    ("a full day", "The battery lasts a full day", "unknown", None),
])
def test_extended_use_requires_explicit_ownership(raw, transcript, expected_type, days):
    review = captured()["source_analyses"][0]
    allowed = set(SourceAnalysisDraft.model_fields)
    payload = {k: v for k, v in review.items() if k in allowed}
    for claim in payload["claims"]:
        for ref in claim["evidence"]: ref.pop("evidence_node_id")
    draft = SourceAnalysisDraft.model_validate(payload)
    draft = draft.model_copy(update={"review_type": "long_term", "usage_period_raw": raw, "usage_period_mentioned": True})
    normalized, _ = normalize_usage(draft, transcript)
    assert normalized.review_type == expected_type and normalized.usage_period_days_estimate == days


def test_snapshot_contracts_and_static_checks_are_honest():
    assert snapshot_output_model("review_analyst", VideoExtraction.model_json_schema()) is VideoExtraction
    assert snapshot_output_model("consensus_analyst", EvidenceBoundBuyingSynthesis.model_json_schema()) is EvidenceBoundBuyingSynthesis
    assert snapshot_input_model("consensus_analyst", CatalogRepairSynthesisInput.model_json_schema()) is CatalogRepairSynthesisInput
    assert AGENT_REGISTRY["review_analyst"].output_model is ClassifiedVideoExtraction
    assert AGENT_REGISTRY["consensus_analyst"].output_model is DistinctBuyingSynthesis
    for spec in AGENT_REGISTRY.values():
        metrics = evaluate_agent_spec(spec)["metrics"]
        assert metrics["validation_kind"] == "static_contract" and not metrics["live_accuracy_verified"]
        assert "central_claim_evidence_linkage" not in metrics


def test_captured_iphone_aperture_survives_but_uncited_comparison_is_pruned():
    capture = json.loads((Path(__file__).parent / "fixtures/live_synthesis_reliability.json").read_text(encoding="utf-8-sig"))
    run = capture["runs"][0]
    diagnostic = []
    draft = FinalReportDraft.model_validate(run["draft"])
    safe, audit, terminal = ground_report(draft, run["source_analyses"], AuditResult(verdict="pass"),
                                         strict_grounding=True, diagnostics=diagnostic)
    original = [*draft.consensus_pros, *draft.consensus_cons]
    retained = [*safe.consensus_pros, *safe.consensus_cons]
    aperture = next(f for f in original if "f/1.8" in f.statement)
    comparison = next(f for f in original if "last year's 18 hours" in f.statement)
    assert aperture in retained and comparison not in retained
    assert audit.verdict == "pass_with_warnings" and not terminal
    assert any("duration_not_cited" in d["reasons"] for d in diagnostic)


def test_owned_quote_product_code_survives_without_matching_requested_name():
    fixture = captured(); payload = response(fixture)
    quotes, bindings, _ = evidence_catalog(fixture["source_analyses"])
    owner, eid = bindings["e1"]
    for review in fixture["source_analyses"]:
        for claim in review["claims"]:
            for ref in claim["evidence"]:
                if ref["evidence_node_id"] == eid:
                    ref["evidence_text"] = "The E1 component has a supported quoted property"
    payload["assertions"][0]["observation"] = "The e1 component has a supported quoted property"
    draft = NormalizedBuyingSynthesis.model_validate(payload).as_report("Phone", "Phone", fixture["source_analyses"])
    assert "e1 component" in draft.consensus_pros[0].statement


def test_executor_retry_includes_index_ownership_and_output_hash(monkeypatch):
    import asyncio
    import uuid
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from app.analysis import executor
    from app.runtime.service import RuntimeTaskError
    from app.runtime.contracts import canonical_json_hash
    fixture = captured(); payload = response(fixture)
    payload["assertions"][0]["evidence_refs"] = ["e9999"]
    result = NormalizedBuyingSynthesis.model_validate(payload)
    db = MagicMock()
    db.get.side_effect = [SimpleNamespace(definition_id=uuid.uuid4()), SimpleNamespace(key="consensus_analyst")]
    monkeypatch.setattr(executor, "session_scope", lambda: nullcontext(db))
    monkeypatch.setattr(executor, "_agent_task_input", lambda *_: ({"product_display_name": "Phone", "product_canonical_name": "Phone"}, ()))
    monkeypatch.setattr(executor, "_call_agent", AsyncMock(return_value=result))
    monkeypatch.setattr(executor, "_outputs_with_prefix", lambda _id, prefix: [{"analysis": r} for r in fixture["source_analyses"]] if prefix.startswith("analyze_review") else [])
    with pytest.raises(RuntimeTaskError) as error:
        asyncio.run(executor._execute_agent(uuid.uuid4(), SimpleNamespace(agent_version_id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4()), None, {}, config=None))
    assert error.value.invalid_output_hash == canonical_json_hash(result.model_dump(mode="json"))
    issue = error.value.validator_results["issues"][0]
    assert issue["loc"] == ["assertions", 0, "evidence_refs"] and issue["ownership"] == "unknown"


@pytest.mark.parametrize("central", [True, False])
def test_empty_normalized_draft_skips_initial_model_audit_and_classifies_repair(monkeypatch, central):
    from types import SimpleNamespace
    from app.analysis import executor
    fixture = captured(); payload = response(fixture)
    for item in payload["assertions"]: item["observation"] = "s99 reports a benefit"
    for review in fixture["source_analyses"]:
        for claim in review["claims"]: claim["central"] = central
    draft = NormalizedBuyingSynthesis.model_validate(payload).as_report("Phone", "Phone", fixture["source_analyses"])
    consensus = {"draft": draft.model_dump(mode="json"), "source_analyses": fixture["source_analyses"]}
    monkeypatch.setattr(executor, "_task_output", lambda *_: consensus)
    monkeypatch.setattr(executor, "_outputs_with_prefix", lambda _id, prefix: [{"analysis": r} for r in fixture["source_analyses"]] if prefix.startswith("analyze_review") else [])
    shortcut, seeds = executor._agent_task_input(AGENT_REGISTRY["quality_auditor"], SimpleNamespace(input_payload={}), SimpleNamespace(id=None))
    assert not seeds and shortcut["_shortcut"]["audit"]["verdict"] == "fail"
    assert shortcut["_shortcut"]["grounding_terminal"] is not central
    assert shortcut["_shortcut"]["audit_diagnostics"]["deterministic_empty_draft"]
