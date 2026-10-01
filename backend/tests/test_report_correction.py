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
from app.analysis.audit import CatalogAuditorInput, CitedAuditorInput, cited_audit_input, compact_audit_input
from app.analysis.contracts import AuditResult, FinalReportDraft, QualityAuditorInput
from app.analysis.grounding import _statement_matches, ground_report
from app.analysis.product_info import ProductExtractionDraft, validate_extraction
from app.analysis.projection import project_claims
from app.analysis.prompting import build_prompt_envelope
from app.analysis.registry import AGENT_REGISTRY, snapshot_input_model, snapshot_output_model
from app.analysis.synthesis import (
    AtomicBuyingSynthesis, AtomicSynthesisInput, BuyingSynthesis, SynthesisInput,
    EvidenceBoundBuyingSynthesis, QuoteSynthesisInput, SourceBoundBuyingSynthesis, SynthesisBindingError, compact_synthesis_input, evidence_catalog, quote_synthesis_input,
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
    assert issubclass(AGENT_REGISTRY["consensus_analyst"].output_model, EvidenceBoundBuyingSynthesis)
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


def test_captured_hp_mixed_fraction_is_equal_without_rounding_or_foreign_support():
    quote = "Omen especially lasts a pretty reasonable 7 and 1/2 hours here"
    assert _statement_matches("Lasts 7.5 hours", "", quote, "HP Omen Max 16")
    assert _statement_matches("Lasts 7 and 1/2 hours", "", "Lasts 7.5 hours", "HP Omen Max 16")
    assert not _statement_matches("Lasts 7 hours", "", quote, "HP Omen Max 16")
    assert not _statement_matches("Lasts 7.4 hours", "", quote, "HP Omen Max 16")
    assert not _statement_matches("Lasts 7.5 hours", "", "Lasts 7 and 1/0 hours", "HP Omen Max 16")
    fixture = captured()
    reviews = fixture["source_analyses"]
    ref = reviews[0]["claims"][0]["evidence"][0]
    ref["evidence_text"] = quote
    draft = split_synthesis(captured()).as_report("HP Omen Max 16", "HP Omen Max 16", reviews).model_dump(mode="json")
    draft["consensus_pros"] = [{"statement": "Lasts 7.5 hours", "source_ids": [reviews[0]["source_id"]],
                                "evidence_node_ids": [ref["evidence_node_id"]]}]
    safe, audit, _ = ground_report(FinalReportDraft.model_validate(draft), reviews, AuditResult(verdict="pass"))
    assert safe.consensus_pros and not any(issue.field_path == "report_draft.consensus_pros[0]" for issue in audit.issues)
    foreign = copy.deepcopy(draft)
    foreign["consensus_pros"][0]["source_ids"] = [reviews[1]["source_id"]]
    safe, audit, _ = ground_report(FinalReportDraft.model_validate(foreign), reviews, AuditResult(verdict="pass"))
    assert not safe.consensus_pros and any(issue.code == "finding_support_mismatch" for issue in audit.issues)


def test_captured_charging_duration_accepts_equivalent_time_without_borrowing_other_quantities():
    fixture = json.loads((Path(__file__).parent / "fixtures/charging_duration_rejection.json").read_text())
    quote = fixture["evidence_text"]
    product = fixture["product_name"]
    assert _statement_matches(fixture["statement"], "", quote, product)
    assert _statement_matches("Charged from 0 to 81% in 30 minutes", "", quote, product)
    assert _statement_matches("Charged from 0 to 81% in 0.5 hours", "", quote, product)
    assert _statement_matches("Charged from 0 to 81% in 30min", "", quote, product)
    assert _statement_matches("Charged from 0 to 81% in 0.5h", "", quote, product)
    assert _statement_matches("Charged from 0 to 81% in 1800s", "", quote, product)
    assert _statement_matches("Half an hour", "", "Took 30 minutes", product)
    for statement in ("Charged to 81% in 30 hours", "Charged to 81% in 30 seconds",
                      "Charged to 80% in 30 minutes", "Charged to 30% in half an hour"):
        assert not _statement_matches(statement, "", quote, product)
    assert not _statement_matches("Charged to 81% in 30 minutes", "", "Battery reached 81% at 30 cycles", product)
    assert not _statement_matches("Battery at 30%", "", "Battery lasted 30 minutes", product)
    assert _statement_matches("Stated as 5.3", "", "Bluetooth version 5.3", product)
    assert _statement_matches("Stated as 3.2 g", "", "Weight is 3.2 g", product)


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
    assert error.value.issue == {"type": "assertion_source_mismatch", "loc": ["assertions", 0],
                                 "reference_category": "citation", "ownership": "mixed"}
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


def test_compact_audit_preserves_original_indices_and_citation_ownership():
    fixture = captured()
    reviews = fixture["source_analyses"]
    draft = split_synthesis(fixture).as_report("T11", "Black Shark T11", reviews)
    original = {"report_draft": draft.model_dump(mode="json"), "source_analyses": reviews}
    compact = CatalogAuditorInput.model_validate(compact_audit_input(original))
    _, bindings, sources = evidence_catalog(reviews)
    catalog = {item.evidence_ref: item for item in compact.evidence_catalog}
    for field in ("consensus_pros", "consensus_cons"):
        for original_item, compact_item in zip(getattr(draft, field), getattr(compact.report_draft, field), strict=True):
            assert compact_item.statement == original_item.statement
            assert [sources[ref] for ref in compact_item.source_refs] == [str(sid) for sid in original_item.source_ids]
            assert [bindings[ref][1] for ref in compact_item.evidence_refs] == [str(eid) for eid in original_item.evidence_node_ids]
            assert all(catalog[ref].source_ref in compact_item.source_refs for ref in compact_item.evidence_refs)
    rendered = compact.model_dump_json()
    assert all(review["source_id"] not in rendered for review in reviews)
    assert len(rendered) < len(json.dumps(original))
    assert snapshot_input_model("quality_auditor", QualityAuditorInput.model_json_schema()) is QualityAuditorInput
    assert snapshot_input_model("quality_auditor", CatalogAuditorInput.model_json_schema()) is CatalogAuditorInput
    broken = copy.deepcopy(original)
    broken["report_draft"]["consensus_pros"][0]["evidence_node_ids"] = [str(uuid.uuid4())]
    with pytest.raises(KeyError):
        compact_audit_input(broken)


def test_unverified_duration_is_not_offered_to_synthesis_or_auditor():
    reviews = copy.deepcopy(captured()["source_analyses"])
    reviews[0].update(usage_period_mentioned=False, usage_period_raw="not specified")
    reviews[1].update(usage_period_mentioned=False, usage_period_raw="about a month")
    reviews[2].update(usage_period_mentioned=True, usage_period_raw="a few days")
    catalog, _, sources = evidence_catalog(reviews)
    by_owner = {sources[item["source_ref"]]: item for item in catalog["sources"]}
    assert by_owner[reviews[0]["source_id"]]["usage_period_raw"] is None
    assert by_owner[reviews[1]["source_id"]]["usage_period_raw"] is None
    assert by_owner[reviews[2]["source_id"]]["usage_period_raw"] == "a few days"


def test_quote_only_synthesis_excludes_broader_derived_claims_and_preserves_legacy():
    reviews = captured()["source_analyses"]
    reviews[0]["claims"][0]["claim"] = "Unsupported OLED resolution 2560x1600 and refresh rate 240 Hz"
    original = {"product_display_name": "T11", "product_canonical_name": "Black Shark T11",
                "requested_source_count": 5, "source_analyses": reviews}
    successor = QuoteSynthesisInput.model_validate(quote_synthesis_input(original))
    assert "Unsupported OLED" not in successor.model_dump_json()
    assert all("claim" not in item for item in successor.model_dump(mode="json")["evidence_catalog"])
    legacy = AtomicSynthesisInput.model_validate(compact_synthesis_input(original))
    assert "Unsupported OLED" in legacy.model_dump_json()
    assert snapshot_input_model("consensus_analyst", legacy.model_json_schema()) is AtomicSynthesisInput
    assert snapshot_input_model("consensus_analyst", successor.model_json_schema()) is QuoteSynthesisInput


def test_cited_auditor_receives_exact_quotes_beside_each_original_finding():
    reviews = captured()["source_analyses"]
    draft = split_synthesis(captured()).as_report("T11", "Black Shark T11", reviews)
    original = {"report_draft": draft.model_dump(mode="json"), "source_analyses": reviews}
    catalog = CatalogAuditorInput.model_validate(compact_audit_input(original))
    successor = CitedAuditorInput.model_validate(cited_audit_input(original))
    by_ref = {item.evidence_ref: item for item in catalog.evidence_catalog}
    for field in ("consensus_pros", "consensus_cons"):
        for before, after in zip(getattr(catalog.report_draft, field), getattr(successor.report_draft, field), strict=True):
            assert (after.statement, after.source_refs) == (before.statement, before.source_refs)
            assert [ref.evidence_ref for ref in after.citations] == list(before.evidence_refs)
            assert all(ref.excerpt == by_ref[ref.evidence_ref].excerpt for ref in after.citations)
            assert all(ref.source_ref in after.source_refs for ref in after.citations)
    assert len(successor.model_dump_json()) < len(catalog.model_dump_json())
    assert snapshot_input_model("quality_auditor", successor.model_json_schema()) is CitedAuditorInput
    assert snapshot_input_model("quality_auditor", catalog.model_json_schema()) is CatalogAuditorInput
    broken = copy.deepcopy(original)
    broken["report_draft"]["consensus_cons"][0]["evidence_node_ids"] = [str(uuid.uuid4())]
    with pytest.raises(KeyError):
        cited_audit_input(broken)


def test_inline_disagreement_evidence_preserves_each_side_and_absent_extra_context():
    fixture = captured()
    draft = split_synthesis(fixture).as_report("T11", "Black Shark T11", fixture["source_analyses"])
    payload = draft.model_dump(mode="json")
    payload["disagreements"] = [{"topic": "Fit", "side_a": "Comfortable fit", "side_b": "Uncomfortable fit",
        "side_a_source_ids": [fixture["source_analyses"][0]["source_id"]],
        "side_b_source_ids": [fixture["source_analyses"][1]["source_id"]]}]
    inline = CitedAuditorInput.model_validate(cited_audit_input({"report_draft": payload,
                                                               "source_analyses": fixture["source_analyses"]}))
    disagreement = inline.report_draft.disagreements[0]
    assert disagreement.side_a_citations and disagreement.side_b_citations
    assert all(item.source_ref in disagreement.side_a_source_refs for item in disagreement.side_a_citations)
    assert all(item.source_ref in disagreement.side_b_source_refs for item in disagreement.side_b_citations)
    spec = AGENT_REGISTRY["quality_auditor"]
    prompt = build_prompt_envelope(spec, task_instruction=spec.purpose, task_input=inline.model_dump(mode="json"),
                                  context_manifest_id=None, rendered_context="<no-authorized-context />")
    assert "<no-authorized-context />" not in prompt.user
    assert "citations" in prompt.user and "server-bound owners" in prompt.system


def test_hp_exact_supported_quotes_remain_available_in_inline_audit():
    # Bounded regression excerpts from the failed HP run; no transcript or raw response export.
    reviews = copy.deepcopy(captured()["source_analyses"][:1])
    review = reviews[0]
    quotes = ("the RAM is fully upgradable, which is really cool",
              "It lasted less than 3 hours in the UL Procyon Office productivity battery drain benchmark")
    for index, quote in enumerate(quotes):
        claim = copy.deepcopy(review["claims"][0])
        claim["evidence"] = [{**claim["evidence"][0], "evidence_text": quote,
                              "evidence_node_id": str(uuid.uuid4())}]
        review["claims"][index] = claim
    payload = split_synthesis(captured()).as_report("T11", "Black Shark T11", captured()["source_analyses"]).model_dump(mode="json")
    payload.update(product_display_name="HP Omen 16 Max", product_canonical_name="HP Omen 16 Max",
                   consensus_pros=[{"statement": "RAM is fully upgradable", "source_ids": [review["source_id"]],
                                    "evidence_node_ids": [review["claims"][0]["evidence"][0]["evidence_node_id"]]}],
                   consensus_cons=[{"statement": "Lasted less than 3 hours in the UL Procyon Office benchmark",
                                    "source_ids": [review["source_id"]],
                                    "evidence_node_ids": [review["claims"][1]["evidence"][0]["evidence_node_id"]]}],
                   longest_usage_period=None, longest_usage_source_id=None)
    inline = CitedAuditorInput.model_validate(cited_audit_input({"report_draft": payload, "source_analyses": reviews}))
    assert inline.report_draft.consensus_pros[0].citations[0].excerpt == quotes[0]
    assert inline.report_draft.consensus_cons[0].citations[0].excerpt == quotes[1]
    # Deterministic grounding still rejects unsupported quantities and trusts no model approval to add evidence.
    payload["consensus_pros"][0]["statement"] = "RAM is fully upgradable to 128 GB"
    _, audit, _ = ground_report(FinalReportDraft.model_validate(payload), reviews, AuditResult(verdict="pass"))
    assert any(issue.code == "finding_support_mismatch" for issue in audit.issues)


def test_uppercase_audit_issues_prune_unsupported_material_without_approving_failure():
    fixture = captured()
    draft = split_synthesis(fixture).as_report("T11", "Black Shark T11", fixture["source_analyses"])
    rejected = AuditResult.model_validate({"verdict": "fail", "issues": [
        {"code": "UNSUPPORTED_FINDING", "field_path": f"report_draft.{field}[{index}]"}
        for field in ("consensus_pros", "consensus_cons")
        for index, _ in enumerate(getattr(draft, field))]})
    safe, audit, terminal = ground_report(draft, fixture["source_analyses"], rejected, strict_grounding=True)
    assert not safe.consensus_pros and not safe.consensus_cons
    assert audit.verdict == "fail" and not terminal
    assert {issue.code for issue in audit.issues} == {"unsupported_finding", "grounded_conclusion_missing"}
    unknown = AuditResult.model_validate({"verdict": "fail", "issues": [
        {"code": "UNKNOWN_SAFETY_FAILURE", "field_path": "report_draft"}]})
    _, audit, terminal = ground_report(draft, fixture["source_analyses"], unknown, strict_grounding=True)
    assert audit.verdict == "fail" and terminal


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
