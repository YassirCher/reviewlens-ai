"""Twenty matched captured-input replays with local fixtures, never live inference.

Measures envelopes, schemas, completions, retries, repair and local processing.
Human-labelled fixture decisions check contract handling, not model accuracy.
"""
from __future__ import annotations

import asyncio
import copy
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.analysis.audit import (
    CitedAuditorInput,
    DecisionAuditorInput,
    FindingAuditResult,
    cited_audit_input,
    decision_audit_input,
    finding_audit_schema,
)
from app.analysis.contracts import AuditResult
from app.analysis.grounding import ground_report
from app.analysis.prompting import build_prompt_envelope
from app.analysis.registry import AGENT_REGISTRY, UNIVERSAL_POLICY
from app.analysis.synthesis import (
    QuoteSynthesisInput,
    SourceBoundBuyingSynthesis,
    evidence_bound_synthesis_schema,
    evidence_catalog,
    quote_synthesis_input,
    catalog_repair_synthesis_input,
    unchanged_rejected_findings,
)
from app.knowledge.retrieval import estimate_tokens
from compare_buying_report_budget import compare

OBSERVATIONS = (
    "The reviewer reports not a single scratch on the display.",
    "The reviewer reports more scratch-resistant glass and no scratches.",
    "The reviewer reports greater micro-scratch resistance and choosing no screen protector.",
    "The reviewer reports about 6 hours of screen-on time.",
    "The reviewer reports 60% to 70% battery on normal days and two days on a charge.",
    "The reviewer reports excellent standby time and that most users will not drain it in one day.",
    "The reviewer describes battery life as superb.",
    "The reviewer reports 100% battery capacity after 177 cycles.",
    "The reviewer's blue model shows camera-area wear; their silver model shows no wear.",
    "The reviewer reports soft aluminum that dents easily.",
    "The reviewer reports a slippery phone and a dent, without establishing its cause.",
    "The reviewer reports that the soft aluminum and rounded corners make the phone slippery.",
)


def response_for(draft, reviews, candidate):
    _, bindings, sources = evidence_catalog(reviews)
    refs = {eid: ref for ref, (_, eid) in bindings.items()}
    owners = {owner: ref for ref, owner in sources.items()}
    assertions = []
    for kind, field in (("strength", "consensus_pros"), ("caveat", "consensus_cons")):
        for finding in draft[field]:
            attribute, _, observation = finding["statement"].partition(": ")
            assertion = {"kind": kind, "attribute": attribute, "observation": observation,
                         "evidence_refs": [refs[eid] for eid in finding["evidence_node_ids"]]}
            if not candidate:
                assertion["source_ref"] = owners[finding["source_ids"][0]]
            assertions.append(assertion)
    return {"summary": draft["summary"], "assertions": assertions,
            "limitations": ["Individual reviewer observations; transcript evidence only."]}


async def replay(fixture, case, candidate):
    reviews = copy.deepcopy(fixture["source_analyses"])
    reviews = reviews[case % 5:] + reviews[:case % 5]
    raw = {"product_display_name": "iPhone 17 Pro Max", "product_canonical_name": "iphone 17 pro max",
           "requested_source_count": 5, "source_analyses": reviews}
    ledger = {"total_tokens": 0, "model_call_count": 0}
    started = time.perf_counter()

    async def shared(tokens):
        ledger["total_tokens"] += tokens
        ledger["model_call_count"] += 1
        await asyncio.sleep(.002)

    # Common-call costs are captured usage; no changes to acquisition/review calls.
    common = 92242 - (5764 + 5566 + 3185 + 6811 + 3187)
    await shared(679)
    await shared(5209)
    await asyncio.gather(*(shared(tokens) for tokens in
        (10000, 10000, 10000, 10000, common - 5888 - 40000)))

    async def call(role, payload, output):
        spec = AGENT_REGISTRY[role]
        if not candidate:
            old = fixture["baseline_specs"][role]
            spec = replace(spec, role_prompt=old["system_prompt"].removeprefix(UNIVERSAL_POLICY + "\n\n"),
                input_model=QuoteSynthesisInput if role == "consensus_analyst" else CitedAuditorInput,
                output_model=SourceBoundBuyingSynthesis if role == "consensus_analyst" else AuditResult)
        if role == "consensus_analyst":
            payload = catalog_repair_synthesis_input(payload) if candidate else quote_synthesis_input(payload)
        else:
            payload = decision_audit_input(payload) if candidate else cited_audit_input(payload)
        validated = spec.input_model.model_validate(payload)
        envelope = build_prompt_envelope(spec, task_instruction=spec.purpose,
            task_input=validated.model_dump(mode="json"), context_manifest_id=None, rendered_context="<no-authorized-context />")
        prompt = estimate_tokens(envelope.system) + estimate_tokens(envelope.user)
        assert prompt <= spec.max_input_tokens
        schema = (finding_audit_schema(validated) if candidate and role == "quality_auditor" else
                  evidence_bound_synthesis_schema(validated) if candidate else spec.output_model.model_json_schema())
        ledger["total_tokens"] += prompt + estimate_tokens(json.dumps(schema)) + estimate_tokens(json.dumps(output))
        ledger["model_call_count"] += 1
        await asyncio.sleep(.002)
        result = spec.output_model.model_validate(output)
        return result.as_audit(validated) if isinstance(result, FindingAuditResult) else result

    narrowed = copy.deepcopy(fixture["draft"])
    for index, finding in enumerate((*narrowed["consensus_pros"], *narrowed["consensus_cons"])):
        finding["statement"] = finding["statement"].split(":", 1)[0].replace("_", " ") + ": " + OBSERVATIONS[index]
    narrowed.update(summary="Reviewers describe battery and scratch-resistance strengths, with slippery bodies and finish-wear caveats.",
                    longest_usage_period=None, longest_usage_source_id=None, who_should_buy=[], who_should_avoid=[])
    repair = case % 4 == 0
    drafted = copy.deepcopy(narrowed if candidate else fixture["draft"])
    if candidate and repair:
        drafted["consensus_cons"][2]["statement"] += " after a 10-foot drop"
    output = response_for(drafted, reviews, candidate)
    if not candidate:
        # The original run spent a binding-invalid synthesis attempt before success.
        await call("consensus_analyst", raw, output)
    result = await call("consensus_analyst", raw, output)
    report = result.as_report(raw["product_display_name"], raw["product_canonical_name"], reviews)

    async def audit(report, reject):
        audit_payload = {"report_draft": report.model_dump(mode="json"), "source_analyses": reviews}
        if candidate:
            supplied = DecisionAuditorInput.model_validate(decision_audit_input(audit_payload))
            checks = []
            for finding in (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons):
                bad = reject and finding.field_path == "report_draft.consensus_cons[2]"
                checks.append({"field_path": finding.field_path, "supported": not bad,
                    "evidence_refs": [q.evidence_ref for q in finding.citations],
                    "category": "condition" if bad else None, "unsupported_clause": "after a 10-foot drop" if bad else None,
                    "explanation": "The quoted source reports a dent but does not establish a drop or its height." if bad else None})
            verdict, diagnostics = await call("quality_auditor", audit_payload, {"finding_checks": checks, "other_issues": []})
        else:
            verdict = await call("quality_auditor", audit_payload, fixture["audit"])
            diagnostics = {}
        return verdict, diagnostics

    model_audit, diagnostics = await audit(report, repair)
    safe, verdict, _ = ground_report(report, reviews, model_audit, strict_grounding=True)
    if not candidate or repair:
        corrected = narrowed if candidate else fixture["correction_draft"]
        repair_input = {**raw, "report_under_repair": report.model_dump(mode="json"),
            "correction_issues": verdict.model_dump(mode="json")["issues"], "audit_diagnostics": diagnostics}
        result = await call("consensus_analyst", repair_input, response_for(corrected, reviews, candidate))
        changed = result.as_report(raw["product_display_name"], raw["product_canonical_name"], reviews)
        if candidate:
            assert not unchanged_rejected_findings(report.model_dump(mode="json"), changed, repair_input["correction_issues"])
        model_audit, _ = await audit(changed, False)
        safe, verdict, _ = ground_report(changed, reviews, model_audit, strict_grounding=True)
    retained = [*safe.consensus_pros, *safe.consensus_cons]
    if candidate:
        # Manually labelled narrow observations must remain exactly intact; no invented condition survives.
        assert [finding.statement for finding in retained] == [f["statement"] for f in (*narrowed["consensus_pros"], *narrowed["consensus_cons"])]
        assert verdict.verdict != "fail"
    else:
        assert verdict.verdict == "fail" and not retained
    return {"case_id": str(case), **ledger, "completion_ms": (time.perf_counter() - started) * 1000,
        "source_count_requested": 5, "source_count_analyzed": 5, "quote_valid_rate": 1,
        "unsupported_claim_rate": 0, "buyer_coverage_rate": len(retained) / 12,
        "first_audit_repairable": repair if candidate else True,
        "correction_model_calls": int(repair) if candidate else 1, "reaudit_model_calls": int(repair) if candidate else 1}


async def main():
    fixture = json.loads((ROOT / "backend/tests/fixtures/iphone_audit_rejection.json").read_text(encoding="utf-8-sig"))
    baseline, candidate = {}, {}
    for case in range(20):
        samples = {False: [], True: []}
        for repetition in range(3):
            for changed in ((False, True) if repetition % 2 == 0 else (True, False)):
                samples[changed].append(await replay(fixture, case, changed))
        for changed, rows in samples.items():
            row = dict(rows[0])
            row["completion_ms"] = statistics.median(r["completion_ms"] for r in rows)
            (candidate if changed else baseline)[str(case)] = row
    result = compare(baseline, candidate, strict_audit=True)
    result.update(paid_calls=0, production_parity_verified=False,
                  measurement="Captured common-call usage plus changed-call estimates; local fixture delay and application processing")
    output = ROOT / ".audit-cache/audit-decision-budget.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
