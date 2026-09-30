from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.analysis import executor
from app.analysis.contracts import AuditResult, FinalReportDraft
from app.analysis.grounding import _statement_matches, ground_report
from app.analysis.product_info import ProductExtractionDraft, validate_extraction
from app.analysis.projection import project_claims
from app.analysis.registry import AGENT_REGISTRY, snapshot_input_model, snapshot_output_model
from app.analysis.synthesis import (
    AtomicBuyingSynthesis, AtomicSynthesisInput, BuyingSynthesis, SynthesisInput,
    SourceBoundBuyingSynthesis, SynthesisBindingError, compact_synthesis_input, evidence_catalog,
)
from app.runtime.service import RuntimeTaskError


def captured() -> dict:
    return json.loads((Path(__file__).parent / "fixtures/report_correction_5_sources.json").read_text(encoding="utf-8-sig"))


def product_draft(fixture: dict) -> dict:
    review = fixture["source_analyses"][2]
    facts = []
    for index, label, value in [(1, "Weight", "3.2 g"), (2, "Battery warning", "10%"),
                                (3, "Claimed battery life", "30 hours"), (4, "Bluetooth", "5.3")]:
        quote = review["claims"][index]["evidence"][0]
        facts.append({"group": "Product", "label": label, "value": value,
                      "evidence": {"source_part": "transcript", "excerpt": quote["evidence_text"],
                                   "timestamp_seconds": quote["timestamp_start_seconds"]}})
    return {"facts": facts}


def split_synthesis(fixture: dict) -> AtomicBuyingSynthesis:
    reviews = fixture["source_analyses"]
    _, bindings, _ = evidence_catalog(reviews)
    reverse = {eid: key for key, (_, eid) in bindings.items()}

    def ref(source: int, claim: int, quote: int = 0) -> str:
        return reverse[reviews[source]["claims"][claim]["evidence"][quote]["evidence_node_id"]]

    return AtomicBuyingSynthesis.model_validate({
        "summary": "Reviewers describe comfortable fit, with an ear-specific fit caveat and game-specific latency.",
        "assertions": [
            {"kind": "strength", "attribute": "Earbud weight", "observation": "Stated as 3.2 g",
             "evidence_refs": [ref(2, 1)]},
            {"kind": "strength", "attribute": "Comfort", "observation": "The reviewer found the earbuds comfortable",
             "evidence_refs": [ref(4, 1)]},
            {"kind": "strength", "attribute": "Battery life", "observation": "Claimed up to 30 hours",
             "conditions": "with the charging case", "evidence_refs": [ref(2, 3)]},
            {"kind": "strength", "attribute": "Bluetooth version", "observation": "Stated as 5.3",
             "evidence_refs": [ref(2, 4)]},
            {"kind": "strength", "attribute": "Gaming latency", "observation": "The reviewer reported no lag",
             "conditions": "during their gaming test", "evidence_refs": [ref(3, 0, 1)]},
            {"kind": "caveat", "attribute": "Latency", "observation": "The reviewer reported a 0.1-second delay",
             "conditions": "in Free Fire", "evidence_refs": [ref(1, 0)]},
            {"kind": "caveat", "attribute": "Case mechanism", "observation": "The reviewer described looseness",
             "evidence_refs": [ref(0, 4)]},
        ],
    })


def test_captured_cross_segment_facts_survive_and_bad_details_are_independent():
    fixture = captured()
    caption = fixture["captions"][2]
    draft = product_draft(fixture)
    bad = copy.deepcopy(draft["facts"][0])
    bad["value"] = "13.2 g"
    distant = copy.deepcopy(draft["facts"][1])
    distant["evidence"]["timestamp_seconds"] = 900
    foreign = copy.deepcopy(draft["facts"][0])
    foreign["evidence"]["excerpt"] = "A foreign reviewer described excellent insulation."
    foreign["value"] = "excellent insulation"
    scoped = copy.deepcopy(draft["facts"][0])
    scoped["scope"] = "Black Shark T12"
    draft["facts"].extend([bad, distant, foreign, scoped])
    diagnostics = []
    facts, variants, sample = validate_extraction(ProductExtractionDraft.model_validate(draft),
        title=caption["title"], description=caption["description"], transcript_body=caption["body"],
        video_id=caption["video_id"], canonical_product="Black Shark T11", diagnostics=diagnostics)
    assert [fact.value for fact in facts] == ["3.2 g", "10%", "30 hours", "5.3"]
    assert variants == () and sample.units == ()
    assert len(diagnostics) == 4 and diagnostics[0]["code"] == "value_not_supported"
    assert set(diagnostics[0]) == {"path", "code"}


def test_short_catalog_binds_split_observations_without_borrowing_quantities():
    fixture = captured()
    reviews = fixture["source_analyses"]
    synthesis = split_synthesis(fixture)
    draft = synthesis.as_report("blackshark t11", "Black Shark T11", reviews)
    safe, audit, _ = ground_report(draft, reviews, AuditResult(verdict="pass"))
    assert audit.verdict == "pass" and len(safe.consensus_pros) == 5 and len(safe.consensus_cons) == 2
    assert len(safe.consensus_pros[0].source_ids) == 1
    assert safe.consensus_pros[0].source_ids != safe.consensus_pros[1].source_ids
    compound = draft.model_copy(update={"consensus_pros": (
        draft.consensus_pros[0].model_copy(update={
            "source_ids": (*draft.consensus_pros[0].source_ids, *draft.consensus_pros[1].source_ids),
            "evidence_node_ids": (*draft.consensus_pros[0].evidence_node_ids, *draft.consensus_pros[1].evidence_node_ids)}),)})
    safe, audit, _ = ground_report(compound, reviews, AuditResult(verdict="pass"))
    assert safe.consensus_pros == () and audit.verdict == "pass_with_warnings"


def test_catalog_has_no_uuid_citations_and_rejects_unknown_references():
    fixture = captured()
    payload = {"product_display_name": "blackshark t11", "product_canonical_name": "Black Shark T11",
               "requested_source_count": 5, "source_analyses": fixture["source_analyses"],
               "report_under_repair": fixture["draft"]}
    compact = AtomicSynthesisInput.model_validate(compact_synthesis_input(payload))
    rendered = compact.model_dump_json()
    assert all(review["source_id"] not in rendered for review in fixture["source_analyses"])
    assert len({item.evidence_ref for item in compact.evidence_catalog}) == len(compact.evidence_catalog)
    synthesis = split_synthesis(fixture)
    invalid = synthesis.model_copy(update={"assertions": (
        synthesis.assertions[0].model_copy(update={"evidence_refs": ("e999999",)}),)})
    with pytest.raises(ValueError, match="unknown"):
        invalid.as_report("blackshark t11", "Black Shark T11", fixture["source_analyses"])
    assert snapshot_input_model("consensus_analyst", SynthesisInput.model_json_schema()) is SynthesisInput
    assert snapshot_output_model("consensus_analyst", BuyingSynthesis.model_json_schema()) is BuyingSynthesis
    assert snapshot_output_model("consensus_analyst", FinalReportDraft.model_json_schema()) is FinalReportDraft
    assert AGENT_REGISTRY["consensus_analyst"].output_model is SourceBoundBuyingSynthesis
    assert snapshot_output_model("consensus_analyst", AtomicBuyingSynthesis.model_json_schema()) is AtomicBuyingSynthesis
    with pytest.raises(ValidationError):
        AtomicBuyingSynthesis.model_validate({"summary": "Narrative only", "assertions": []})


def test_measurements_are_not_model_codes_and_semantic_negation_stays_audited():
    assert _statement_matches("Case capacity is 500 mAh", "", "500mAh charging case", "T11")
    assert _statement_matches("A 0.1-second delay", "", "There was a 0.1-second delay", "T11")
    assert _statement_matches("Low latency", "", "There is absolutely no lag", "T11")
    assert not _statement_matches("A T12 specification", "", "The T11 specification", "T11")
    assert not _statement_matches("Capacity 500 mAh", "", "Capacity 1500 mAh", "T11")
    fixture = captured()
    draft = split_synthesis(fixture).as_report("blackshark t11", "Black Shark T11", fixture["source_analyses"])
    safe, audit, _ = ground_report(draft, fixture["source_analyses"], AuditResult.model_validate({
        "verdict": "fail", "issues": [{"code": "unsupported_finding", "field_path": "report_draft.consensus_pros[4]"}]}))
    assert len(safe.consensus_pros) == 4 and audit.verdict == "pass_with_warnings"


def test_source_bound_successor_rejects_mixed_citations_and_internal_labels():
    fixture = captured()
    reviews = fixture["source_analyses"]
    payload = split_synthesis(fixture).model_dump(mode="json")
    _, bindings, sources = evidence_catalog(reviews)
    owner_refs = {owner: ref for ref, owner in sources.items()}
    for item in payload["assertions"]:
        item["source_ref"] = owner_refs[bindings[item["evidence_refs"][0]][0]]
    valid = SourceBoundBuyingSynthesis.model_validate(payload)
    draft = valid.as_report("blackshark t11", "Black Shark T11", reviews)
    assert ground_report(draft, reviews, AuditResult(verdict="pass"))[1].verdict == "pass"
    mixed = copy.deepcopy(payload)
    mixed["assertions"][0]["evidence_refs"].append(payload["assertions"][1]["evidence_refs"][0])
    with pytest.raises(SynthesisBindingError) as error:
        SourceBoundBuyingSynthesis.model_validate(mixed).as_report("T11", "T11", reviews)
    assert error.value.issue == {"type": "assertion_source_mismatch", "loc": ["assertions", 0]}
    label = copy.deepcopy(payload)
    label["assertions"][0]["observation"] = "s1 states 3.2 g"
    with pytest.raises(SynthesisBindingError, match="catalog_label_in_prose"):
        SourceBoundBuyingSynthesis.model_validate(label).as_report("T11", "T11", reviews)
    unknown = copy.deepcopy(payload)
    unknown["assertions"][0]["evidence_refs"] = ["e9999"]
    with pytest.raises(SynthesisBindingError, match="unknown_reference"):
        SourceBoundBuyingSynthesis.model_validate(unknown).as_report("T11", "T11", reviews)
    # An apparently supported narrative cannot override failed semantic re-audit.
    rejected = AuditResult.model_validate({"verdict": "fail", "issues": [
        {"code": "unsupported_finding", "field_path": f"report_draft.{field}[{index}]"}
        for field in ("consensus_pros", "consensus_cons")
        for index, _ in enumerate(getattr(draft, field))]})
    assert ground_report(draft, reviews, rejected)[1].verdict == "fail"


def test_comparison_identity_is_not_a_product_detail_with_missing_scope():
    diagnostics = []
    title = "Black Shark T11 Review en Español (versus Lucifer T6)"
    draft = ProductExtractionDraft.model_validate({"facts": [
        {"group": "product_identity", "label": "product_name", "value": "Black Shark T11",
         "evidence": {"source_part": "title", "excerpt": title}},
        {"group": "product_identity", "label": "compared_product_name", "value": "Lucifer T6",
         "evidence": {"source_part": "title", "excerpt": "Lucifer T6"}},
    ]})
    facts, _, _ = validate_extraction(draft, title=title, description="", transcript_body="",
        video_id="E_nBOaQA_qQ", canonical_product="Black Shark T11", diagnostics=diagnostics)
    assert [fact.value for fact in facts] == ["Black Shark T11"]
    assert diagnostics == [{"path": "facts[1]", "code": "sibling_model"}]


def test_projection_merges_exact_duplicates_and_preserves_opposing_evidence():
    reviews = copy.deepcopy(captured()["source_analyses"][:2])
    for review in reviews:
        review["claims"] = [review["claims"][0]]
        review["claims"][0]["claim"] = "The reviewer reports good sound."
    plan = project_claims(reviews)
    assert len(plan.findings) == 1 and plan.findings[0].relation == "consensus"
    assert len(plan.findings[0].source_ids) == 2
    reviews[1]["channel_id"] = reviews[0]["channel_id"]
    assert project_claims(reviews).findings[0].relation == "scope"
    reviews[1]["claims"][0]["evidence"].append({**reviews[1]["claims"][0]["evidence"][0],
        "evidence_node_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "support_type": "contradicts"})
    assert project_claims(reviews).findings[0].relation == "disagreement"
    reviews[1]["claims"][0]["claim"] = "The reviewer reports poor sound."
    assert len(project_claims(reviews).findings) == 2
    reviews[1]["claims"][0]["evidence"][0]["source_node_id"] = reviews[0]["source_id"]
    with pytest.raises(ValueError, match="source mismatch"):
        project_claims(reviews)


def test_project_handler_never_calls_a_model(monkeypatch):
    fixture = captured()
    monkeypatch.setattr(executor, "_outputs_with_prefix", lambda *_: [{"analysis": r} for r in fixture["source_analyses"]])
    model = AsyncMock(side_effect=AssertionError("deterministic projection called a model"))
    monkeypatch.setattr(executor.OpenRouterGateway, "chat", model)
    monkeypatch.setattr(executor, "_postprocess_knowledge", lambda *_args, **kw: {"owners": kw["evidence_owners"]})
    result = executor._project_knowledge(None, SimpleNamespace(id=None), config=None)
    assert result["owners"] and not model.called


def test_projection_requires_directed_assigned_source_lineage():
    workspace, source_id, evidence_id, source_version, transcript_version = (uuid.uuid4() for _ in range(5))
    source = SimpleNamespace(workspace_id=workspace, status="active", node_type="source", current_version_id=source_version)
    transcript = SimpleNamespace(node_id=uuid.uuid4())
    node = SimpleNamespace(status="active", node_type="transcript", current_version_id=transcript_version)
    db = MagicMock()
    db.get.side_effect = [source, transcript, node]
    db.scalars.return_value.all.return_value = [transcript_version]
    db.scalar.return_value = uuid.uuid4()
    evidence = SimpleNamespace(current_version_id=evidence_id)
    executor._verify_projection_lineage(db, workspace, evidence, source_id)
    db.get.side_effect = [source, transcript, node]
    db.scalar.return_value = None
    with pytest.raises(RuntimeTaskError, match="finding_lineage_invalid"):
        executor._verify_projection_lineage(db, workspace, evidence, source_id)
