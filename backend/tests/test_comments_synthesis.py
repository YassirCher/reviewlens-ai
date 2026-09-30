"""Captured comments-enabled input regressions; no live model accuracy claims."""
import copy
import json
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.analysis.contracts import AuditResult
from app.analysis.grounding import ground_report
from app.analysis.registry import snapshot_input_model
from app.analysis.synthesis import (
    CatalogRepairSynthesisInput, EvidenceBoundBuyingSynthesis, RepairSynthesisInput,
    SynthesisBindingError, catalog_repair_synthesis_input, evidence_bound_synthesis_schema,
    evidence_catalog, repair_synthesis_input,
)
from app.llmops.contracts import strictify_json_schema


def captured():
    return json.loads((Path(__file__).parent / "fixtures/comments_synthesis_failure.json").read_text(encoding="utf-8-sig"))


def raw_input(fixture):
    return {"product_display_name": fixture["product_name"], "product_canonical_name": fixture["product_name"],
            "requested_source_count": 5, "source_analyses": fixture["source_analyses"],
            "audience_analyses": fixture["audience_analyses"]}


def quoted_response(fixture):
    reviews = fixture["source_analyses"]
    _, bindings, _ = evidence_catalog(reviews)
    reverse = {eid: ref for ref, (_, eid) in bindings.items()}
    assertions = []
    for index, (kind, attribute, observation, claim) in enumerate((
        ("strength", "Battery", "The reviewer reports easily lasting all day", 0),
        ("caveat", "Upgrade changes", "The reviewer describes the bare minimum, a spec bump", 0),
        ("strength", "Camera", "The reviewer reports a great all-round camera package", 0),
        ("strength", "Battery test", "The reviewer reports just under 9 hours of screen-on time and 30 to 40% left at day's end", 1),
        ("caveat", "Battery", "The reviewer calls S25 Ultra battery life merely fine, not ultra", 0),
    )):
        quote = reviews[index]["claims"][claim]["evidence"][0]
        assertions.append({"kind": kind, "attribute": attribute, "observation": observation,
            "conditions": "at the default screen resolution" if index == 3 else None,
            "evidence_refs": [reverse[quote["evidence_node_id"]]]})
    return {"summary": "Reviewers report camera and endurance benefits, with a minor-upgrade and battery caveat.",
        "assertions": assertions, "disagreements": [], "longest_usage_period": None, "longest_usage_source_ref": None,
        "who_should_buy": [], "who_should_avoid": [], "limitations": []}


def test_captured_audience_signals_use_the_review_catalog_and_preserve_sampling():
    fixture = captured()
    raw = raw_input(fixture)
    supplied = CatalogRepairSynthesisInput.model_validate(catalog_repair_synthesis_input(raw))
    _, _, sources = evidence_catalog(raw["source_analyses"])
    encoded = supplied.model_dump_json()
    assert len(supplied.audience_analyses) == 5
    for original, mapped in zip(raw["audience_analyses"], supplied.audience_analyses):
        assert sources[mapped.source_ref] == original["source_id"]
        assert mapped.comments_retained == original["comments_retained"]
        assert list(mapped.sampling_limitations) == original["sampling_limitations"]
        assert list(mapped.recurring_cons) == original["recurring_cons"]
        assert original["source_id"] not in encoded and original["audience_signal_node_id"] not in encoded
    assert snapshot_input_model("consensus_analyst", supplied.model_json_schema()) is CatalogRepairSynthesisInput
    legacy = RepairSynthesisInput.model_validate(repair_synthesis_input(raw))
    assert snapshot_input_model("consensus_analyst", legacy.model_json_schema()) is RepairSynthesisInput
    assert str(legacy.audience_analyses[0].source_id) in legacy.model_dump_json()
    foreign = copy.deepcopy(raw["audience_analyses"][0])
    foreign["source_id"] = str(uuid.UUID(int=987))
    raw["audience_analyses"].append(foreign)
    assert len(CatalogRepairSynthesisInput.model_validate(catalog_repair_synthesis_input(raw)).audience_analyses) == 5


def test_every_synthesis_reference_field_is_constrained_to_the_authorized_catalog():
    fixture = captured()
    supplied = CatalogRepairSynthesisInput.model_validate(catalog_repair_synthesis_input(raw_input(fixture)))
    validator = Draft202012Validator(strictify_json_schema(evidence_bound_synthesis_schema(supplied)))
    response = quoted_response(fixture)
    assert not list(validator.iter_errors(response))
    response["longest_usage_source_ref"] = fixture["audience_analyses"][0]["source_id"]
    assert list(validator.iter_errors(response))
    response["longest_usage_source_ref"] = supplied.sources[0].source_ref
    assert not list(validator.iter_errors(response))
    response["disagreements"] = [{"topic": "Battery", "side_a": "Lasts all day", "side_b": "Merely fine",
        "side_a_evidence_refs": response["assertions"][0]["evidence_refs"],
        "side_b_evidence_refs": ["e99999"]}]
    assert list(validator.iter_errors(response))
    response = quoted_response(fixture)
    response["assertions"][0]["evidence_refs"] *= 2
    # Harmless repeated refs reach code normalization rather than causing a gateway retry.
    assert not list(validator.iter_errors(response))


def test_repeated_authorized_references_are_deduplicated_without_losing_caveats_or_ownership():
    fixture = captured()
    response = quoted_response(fixture)
    for item in response["assertions"]:
        item["evidence_refs"] *= 2
    report = EvidenceBoundBuyingSynthesis.model_validate(response).as_report(
        fixture["product_name"], fixture["product_name"], fixture["source_analyses"])
    safe, audit, _ = ground_report(report, fixture["source_analyses"], AuditResult(verdict="pass"), strict_grounding=True)
    assert audit.verdict == "pass"
    assert len(safe.consensus_pros) == 3 and len(safe.consensus_cons) == 2
    findings = (*safe.consensus_pros, *safe.consensus_cons)
    assert len({source_id for item in findings for source_id in item.source_ids}) == 5
    assert all(len(item.evidence_node_ids) == 1 and len(item.source_ids) == 1 for item in findings)


@pytest.mark.parametrize("field", ["longest_usage_source_ref", "side_a_evidence_refs", "side_b_evidence_refs"])
def test_invalid_optional_reference_keeps_a_precise_diagnostic(field):
    fixture = captured()
    response = quoted_response(fixture)
    if field == "longest_usage_source_ref":
        response[field] = "s99"
        path = [field]
    else:
        response["disagreements"] = [{"topic": "Battery", "side_a": "Lasts all day", "side_b": "Merely fine",
            "side_a_evidence_refs": response["assertions"][0]["evidence_refs"],
            "side_b_evidence_refs": response["assertions"][-1]["evidence_refs"]}]
        response["disagreements"][0][field] = ["e99999"]
        path = ["disagreements", 0, field]
    with pytest.raises(SynthesisBindingError) as error:
        EvidenceBoundBuyingSynthesis.model_validate(response).as_report(
            fixture["product_name"], fixture["product_name"], fixture["source_analyses"])
    assert error.value.issue == {"type": "unknown_reference", "loc": path}


def test_duplicate_disagreement_citations_normalize_but_foreign_assertion_ownership_still_fails():
    fixture = captured()
    response = quoted_response(fixture)
    response["disagreements"] = [{"topic": "Battery", "side_a": "Lasts all day", "side_b": "Merely fine",
        "side_a_evidence_refs": response["assertions"][0]["evidence_refs"] * 2,
        "side_b_evidence_refs": response["assertions"][-1]["evidence_refs"] * 2}]
    assert EvidenceBoundBuyingSynthesis.model_validate(response).as_report(
        fixture["product_name"], fixture["product_name"], fixture["source_analyses"]).disagreements
    response["assertions"][0]["evidence_refs"].extend(response["assertions"][-1]["evidence_refs"])
    with pytest.raises(SynthesisBindingError, match="assertion_source_mismatch"):
        EvidenceBoundBuyingSynthesis.model_validate(response).as_report(
            fixture["product_name"], fixture["product_name"], fixture["source_analyses"])
