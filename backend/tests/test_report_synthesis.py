from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.analysis.configuration import _default_workflow
from app.analysis.contracts import AuditResult, ConsensusAnalystInput, FinalReportDraft
from app.analysis.grounding import ground_report
from app.analysis.registry import AGENT_REGISTRY, snapshot_input_model, snapshot_output_model
from app.analysis.synthesis import BuyingSynthesis, SynthesisInput


def captured() -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / "audit_failure_5_sources.json").read_text(encoding="utf-8"))


def narrowed_synthesis(fixture: dict) -> dict:
    reviews = fixture["source_analyses"]
    solid = reviews[3]["claims"][1]
    isolation = reviews[1]["claims"][1]
    return {
        "summary": "One reviewer described a solid build; another reported weak outside-noise isolation.",
        "findings": [
            {"kind": "strength", "statement": "The reviewer described build quality as solid.",
             "source_ids": [reviews[3]["source_id"]],
             "evidence_node_ids": [solid["evidence"][0]["evidence_node_id"]]},
            {"kind": "caveat", "statement": "The semi-in-ear design does not isolate outside noise well.",
             "source_ids": [reviews[1]["source_id"]],
             "evidence_node_ids": [isolation["evidence"][1]["evidence_node_id"]]},
        ],
        "limitations": ["Transcript-only evidence; test conditions differ between reviews."],
    }


def test_captured_narrative_only_correction_is_rejected_by_successor_schema():
    fixture = captured()
    empty = FinalReportDraft.model_validate(fixture["empty_correction"])
    _, audit, _ = ground_report(empty, fixture["source_analyses"], AuditResult(verdict="pass"))
    assert audit.verdict == "fail"
    assert "grounded_conclusion_missing" in {issue.code for issue in audit.issues}
    payload = {"summary": empty.summary, "findings": []}
    assert list(Draft202012Validator(BuyingSynthesis.model_json_schema()).iter_errors(payload))
    with pytest.raises(ValidationError):
        BuyingSynthesis.model_validate(payload)


def test_captured_repair_keeps_atomic_support_and_stable_public_shape():
    fixture = captured()
    synthesis = BuyingSynthesis.model_validate(narrowed_synthesis(fixture))
    draft = synthesis.as_report("blackshark t11", "Black Shark T11")
    safe, audit, terminal = ground_report(draft, fixture["source_analyses"], AuditResult(verdict="pass"))
    assert len(fixture["source_analyses"]) == 5
    assert safe.consensus_pros and safe.consensus_cons and audit.verdict == "pass" and not terminal
    assert "findings" not in safe.model_dump()
    assert safe.longest_usage_period is None and not safe.disagreements
    # A well-formed correction never overrides a real re-audit rejection.
    rejected = AuditResult.model_validate({"verdict": "fail", "issues": [
        {"code": "unsupported_finding", "field_path": "consensus_pros[0].statement"},
        {"code": "unsupported_finding", "field_path": "consensus_cons[0].statement"},
    ]})
    safe, audit, _ = ground_report(draft, fixture["source_analyses"], rejected)
    assert audit.verdict == "fail" and not safe.consensus_pros and not safe.consensus_cons


def test_one_source_can_supply_multiple_cited_quantities_but_cannot_borrow_them():
    fixture = captured()
    review = fixture["source_analyses"][4]
    refs = review["claims"][4]["evidence"]
    synthesis = BuyingSynthesis.model_validate({"summary": "Stated connectivity and driver specs.", "findings": [
        {"kind": "strength", "statement": "The reviewer states Bluetooth 5.3 and a 13 mm driver.",
         "source_ids": [review["source_id"]], "evidence_node_ids": [ref["evidence_node_id"] for ref in refs]},
    ]})
    draft = synthesis.as_report("blackshark t11", "Black Shark T11")
    safe, audit, _ = ground_report(draft, fixture["source_analyses"], AuditResult(verdict="pass"))
    assert safe.consensus_pros and audit.verdict == "pass"
    broken = draft.model_copy(update={"consensus_pros": (draft.consensus_pros[0].model_copy(update={
        "evidence_node_ids": (uuid.UUID(refs[0]["evidence_node_id"]),)
    }),)})
    safe, audit, _ = ground_report(broken, fixture["source_analyses"], AuditResult(verdict="pass"))
    assert not safe.consensus_pros and audit.verdict == "fail"
    # Another source has 13 mm in its quote, but does not repair this source's omission.
    foreign = fixture["source_analyses"][2]["claims"][2]["evidence"][0]["evidence_node_id"]
    broken = broken.model_copy(update={"consensus_pros": (broken.consensus_pros[0].model_copy(update={
        "evidence_node_ids": (*broken.consensus_pros[0].evidence_node_ids, uuid.UUID(foreign))
    }),)})
    assert ground_report(broken, fixture["source_analyses"], AuditResult(verdict="pass"))[1].verdict == "fail"


def test_synthesis_snapshot_adapters_preserve_existing_contracts():
    assert snapshot_input_model("consensus_analyst", ConsensusAnalystInput.model_json_schema()) is ConsensusAnalystInput
    assert snapshot_input_model("consensus_analyst", SynthesisInput.model_json_schema()) is SynthesisInput
    assert snapshot_output_model("consensus_analyst", FinalReportDraft.model_json_schema()) is FinalReportDraft
    assert snapshot_output_model("consensus_analyst", BuyingSynthesis.model_json_schema()) is BuyingSynthesis
    with pytest.raises(ValueError):
        snapshot_input_model("consensus_analyst", {"type": "object"})


def test_repair_and_reaudit_cannot_spend_an_extra_schema_retry():
    agents = {key: SimpleNamespace(id=uuid.uuid4()) for key in AGENT_REGISTRY}
    from app.tools.registry import TOOL_REGISTRY
    tools = {key: SimpleNamespace(id=uuid.uuid4()) for key in TOOL_REGISTRY}
    dag = _default_workflow(agents, tools, run_timeout_seconds=2400)
    templates = {item.template_key: item for item in dag.templates}
    assert templates["correct_consensus"].retry.max_attempts == 1
    assert templates["reaudit_report"].retry.max_attempts == 1
    assert templates["build_consensus"].retry.max_attempts == 2


def test_auditor_protocol_and_scoring_scale_are_explicit():
    prompt = AGENT_REGISTRY["quality_auditor"].role_prompt
    assert "Empty lists/null assert nothing" in prompt
    assert "Combine an owner's cited excerpts" in prompt
    assert "judge support by meaning, including translations" in prompt
    assert "battery runtime" in prompt
    assert "Scores/confidence: integer 0-100" in AGENT_REGISTRY["review_analyst"].role_prompt
