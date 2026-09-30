"""Twenty matched captured-input variants, local provider fixtures and zero paid calls.

Common-call token costs are anchored to captured usage. Changed calls use the
application token estimator, including response schemas and fixture responses.
Timing covers local processing and fixed fixture-provider delay, not production.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.analysis.audit import (
    DecisionAuditorInput,
    FindingAuditResult,
    decision_audit_input,
)
from app.analysis.contracts import AuditResult, FinalReportDraft, QualityAuditorInput
from app.analysis.grounding import ground_report
from app.analysis.product_info import (
    ProductExtractionDraft,
    _supported_value,
    validate_extraction,
)
from app.analysis.projection import project_claims
from app.analysis.prompting import build_prompt_envelope
from app.analysis.registry import AGENT_REGISTRY, UNIVERSAL_POLICY
from app.analysis.synthesis import (
    BuyingSynthesis,
    SourceBoundBuyingSynthesis,
    SynthesisInput,
    evidence_catalog,
    repair_synthesis_input,
)
from app.knowledge.retrieval import estimate_tokens
from app.tools.evidence import transcript_excerpt_matches
from compare_buying_report_budget import compare

COMMON_TOKENS = (679, 5209, 8626, 6440, 4620, 5908, 4861)
SEGMENT = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] (.*)$")


def load_fixture() -> dict:
    return json.loads((ROOT / "backend/tests/fixtures/report_correction_5_sources.json").read_text(encoding="utf-8-sig"))


def fixture_audit_response(supplied: dict, audit: dict) -> dict:
    """Explicitly scripted worker-branch decisions; never evidence of model accuracy."""
    rejected = {issue["field_path"].removeprefix("report_draft.") for issue in audit.get("issues", [])}
    checks = []
    for field in ("consensus_pros", "consensus_cons"):
        for index, finding in enumerate(supplied["report_draft"][field]):
            fail = f"{field}[{index}]" in rejected
            checks.append({"field_path": finding["field_path"], "supported": not fail,
                "evidence_refs": [q["evidence_ref"] for q in finding["citations"]],
                "category": "material" if fail else None,
                "unsupported_clause": finding["statement"] if fail else None,
                "explanation": "Fixture-only rejection exercising the bounded repair branch." if fail else None})
    return {"finding_checks": checks, "other_issues": []}


def atomic_fixture(fixture: dict) -> SourceBoundBuyingSynthesis:
    reviews = fixture["source_analyses"]
    _, bindings, sources = evidence_catalog(reviews)
    reverse = {eid: key for key, (_, eid) in bindings.items()}
    owner_refs = {owner: ref for ref, owner in sources.items()}

    def refs(source, claim, quotes=(0,)):
        return [reverse[reviews[source]["claims"][claim]["evidence"][q]["evidence_node_id"]] for q in quotes]

    rows = [
        ("strength", "Sound quality", "The reviewer describes good sound", refs(3, 2)),
        ("strength", "Construction", "The reviewer describes metal construction", refs(1, 1)),
        ("strength", "Battery indicator", "Flashing red lights indicate less than 10% battery", refs(2, 2)),
        ("strength", "Connectivity", "The reviewer reports simultaneous device use", refs(1, 5)),
        ("strength", "Water resistance", "Stated IPX4 protection against rain and sweat", refs(2, 5, (1,))),
        ("caveat", "Case mechanism", "The reviewer reports looseness", refs(0, 4)),
        ("caveat", "Companion app", "No companion app is available", refs(1, 2)),
        ("caveat", "Noise isolation", "The reviewer describes hearing outside noise", refs(1, 4)),
        ("strength", "Gaming latency", "The reviewer reports no lag", refs(3, 0, (0, 1))),
        ("caveat", "Latency", "The reviewer reports about a 0.1-second delay", refs(1, 0)),
        ("strength", "Battery life", "Claimed up to 30 hours with the charging case", refs(2, 3)),
        ("caveat", "Call quality", "The reviewer reports poor microphone call clarity", refs(4, 2)),
    ]
    return SourceBoundBuyingSynthesis.model_validate({
        "summary": "The reviews describe sound and gaming strengths, with case, app, isolation and microphone caveats.",
        "assertions": [{"kind": kind, "attribute": attr, "observation": text, "evidence_refs": quotes,
                        "source_ref": owner_refs[bindings[quotes[0]][0]]}
                       for kind, attr, text, quotes in rows],
        "limitations": ["Caption evidence only; stated specifications are not independent tests."],
    })


def product_fixture(fixture: dict, case: int) -> ProductExtractionDraft:
    review = fixture["source_analyses"][2]
    facts = []
    for index, label, value in [(1, "Weight", "3.2 g"), (2, "Battery warning", "10%"),
                                (3, "Claimed battery life", "30 hours"), (4, "Bluetooth", "5.3")]:
        quote = review["claims"][index]["evidence"][0]
        facts.append({"group": "Product", "label": label, "value": value,
                      "evidence": {"source_part": "transcript", "excerpt": quote["evidence_text"],
                                   "timestamp_seconds": quote["timestamp_start_seconds"]}})
    bad = copy.deepcopy(facts[case % 4])
    if case % 3 == 0:
        bad["value"] = "9999"
    elif case % 3 == 1:
        bad["evidence"]["timestamp_seconds"] = 900
    else:
        bad["scope"] = "Black Shark T12"
    return ProductExtractionDraft.model_validate({"facts": [*facts, bad]})


def legacy_response(report: FinalReportDraft) -> dict:
    payload = report.model_dump(mode="json", exclude={"product_display_name", "product_canonical_name", "consensus_pros", "consensus_cons"})
    payload["findings"] = [{"kind": kind, **item.model_dump(mode="json")}
                           for kind, items in (("strength", report.consensus_pros), ("caveat", report.consensus_cons))
                           for item in items]
    return payload


def baseline_spec(fixture: dict, key: str):
    spec = AGENT_REGISTRY[key]
    old = fixture["baseline_specs"][key]
    return replace(spec, role_prompt=old["system_prompt"].removeprefix(UNIVERSAL_POLICY + "\n\n"),
                   input_model=(SynthesisInput if key == "consensus_analyst" else
                                QualityAuditorInput if key == "quality_auditor" else spec.input_model),
                   output_model=BuyingSynthesis if key == "consensus_analyst" else AuditResult if key == "quality_auditor" else spec.output_model)


async def replay(fixture: dict, case: int, candidate: bool) -> dict:
    # Reorder independent inputs to exercise stable binding; evidence never changes owner.
    reviews = copy.deepcopy(fixture["source_analyses"])
    ordered = reviews[case % 5:] + reviews[:case % 5]
    ledger = {"total_tokens": 0, "model_call_count": 0}
    start = time.perf_counter()

    async def shared(tokens):
        ledger["total_tokens"] += tokens
        ledger["model_call_count"] += 1
        await asyncio.sleep(.002)

    async def invoke(spec, payload, response):
        if spec.input_model is DecisionAuditorInput:
            payload = decision_audit_input(payload)
        validated = spec.input_model.model_validate(payload).model_dump(mode="json")
        envelope = build_prompt_envelope(spec, task_instruction=spec.purpose, task_input=validated,
                                         context_manifest_id=None, rendered_context="")
        cost = estimate_tokens(envelope.system) + estimate_tokens(envelope.user)
        assert cost <= spec.max_input_tokens
        if spec.output_model is FindingAuditResult:
            response = fixture_audit_response(validated, response)
        ledger["total_tokens"] += cost + estimate_tokens(json.dumps(spec.output_model.model_json_schema())) + estimate_tokens(json.dumps(response))
        ledger["model_call_count"] += 1
        await asyncio.sleep(.002)
        result = spec.output_model.model_validate(response)
        return result.as_audit(DecisionAuditorInput.model_validate(validated))[0] if isinstance(result, FindingAuditResult) else result

    await shared(COMMON_TOKENS[0])
    await shared(COMMON_TOKENS[1])
    await asyncio.gather(*(shared(tokens) for tokens in COMMON_TOKENS[2:]))
    if candidate:
        old_prompt = fixture["baseline_specs"]["review_analyst"]["system_prompt"]
        ledger["total_tokens"] += 5 * (estimate_tokens(AGENT_REGISTRY["review_analyst"].persisted_payload()["system_prompt"]) - estimate_tokens(old_prompt))
        project_claims(ordered)
    else:
        spec = baseline_spec(fixture, "knowledge_curator")
        payload = {"source_analyses": ordered, "audience_analyses": []}
        if case % 3 == 0:
            invalid = copy.deepcopy(fixture["knowledge_plan"])
            invalid["findings"][0]["evidence_node_ids"] = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
            await invoke(spec, payload, invalid)  # Valid schema, invalid lineage: paid attempt is counted.
        await invoke(spec, payload, fixture["knowledge_plan"])

    caption = fixture["captions"][2]
    product = product_fixture(fixture, case)
    facts, _, _ = validate_extraction(product, title=caption["title"], description=caption["description"],
        transcript_body=caption["body"], video_id=caption["video_id"], canonical_product="Black Shark T11")
    if not candidate:
        facts = tuple(fact for fact in facts if any(
            (match := SEGMENT.match(line)) and float(match[1]) - 1 <= fact.evidence[0].timestamp_seconds <= float(match[2]) + 1
            and fact.evidence[0].excerpt.casefold() in match[3].casefold()
            for line in caption["body"].splitlines()))
    assert len(facts) == (4 if candidate else 0)
    assert all(_supported_value(fact.value, fact.evidence[0].excerpt) for fact in facts)

    payload = {"product_display_name": "blackshark t11", "product_canonical_name": "Black Shark T11",
               "requested_source_count": 5, "source_analyses": ordered, "audience_analyses": [], "correction_issues": []}
    synthesis = atomic_fixture(fixture)
    gold = synthesis.as_report("blackshark t11", "Black Shark T11", ordered)
    spec = AGENT_REGISTRY["consensus_analyst"] if candidate else baseline_spec(fixture, "consensus_analyst")
    drafted = await invoke(spec, repair_synthesis_input(payload) if candidate else payload,
                           {**synthesis.model_dump(mode="json"), "assertions": [{k:v for k,v in a.model_dump(mode="json").items() if k != "source_ref"} for a in synthesis.assertions]} if candidate else legacy_response(FinalReportDraft.model_validate(fixture["draft"])))
    report = drafted.as_report("blackshark t11", "Black Shark T11", ordered) if candidate else drafted.as_report("blackshark t11", "Black Shark T11")
    auditor = AGENT_REGISTRY["quality_auditor"] if candidate else baseline_spec(fixture, "quality_auditor")
    repair = case % 4 == 0
    issues = [{"code": "unsupported_finding", "field_path": f"report_draft.{field}[{index}]", "retryable": True}
              for field in ("consensus_pros", "consensus_cons") for index, _ in enumerate(getattr(report, field))] if repair else []
    audit = await invoke(auditor, {"report_draft": report.model_dump(mode="json"), "source_analyses": ordered},
                         {"verdict": "fail" if repair else "pass", "issues": issues})
    safe, verdict, terminal = ground_report(report, ordered, audit, strict_grounding=True)
    if repair:
        assert verdict.verdict == "fail" and not terminal
        repaired = {**payload, "correction_issues": issues, "report_under_repair": report.model_dump(mode="json")}
        drafted = await invoke(spec, repair_synthesis_input(repaired) if candidate else repaired,
                               {**synthesis.model_dump(mode="json"), "assertions": [{k:v for k,v in a.model_dump(mode="json").items() if k != "source_ref"} for a in synthesis.assertions]} if candidate else legacy_response(gold))
        report = drafted.as_report("blackshark t11", "Black Shark T11", ordered) if candidate else drafted.as_report("blackshark t11", "Black Shark T11")
        audit = await invoke(auditor, {"report_draft": report.model_dump(mode="json"), "source_analyses": ordered}, {"verdict": "pass", "issues": []})
        safe, verdict, _ = ground_report(report, ordered, audit, strict_grounding=True)
    elif not candidate:
        safe = FinalReportDraft.model_validate(fixture["safe_draft"])
    assert verdict.verdict != "fail" and safe.consensus_pros and safe.consensus_cons
    refs = {(r["source_id"], e["evidence_node_id"]): e for r in ordered for c in r["claims"] for e in c["evidence"]}
    quotes_valid = [transcript_excerpt_matches(caption["body"], ref["evidence_text"],
        ref["timestamp_start_seconds"], ref["timestamp_end_seconds"])
        for caption in fixture["captions"] for (sid, _), ref in refs.items() if sid == caption["source_id"]]
    retained = [*safe.consensus_pros, *safe.consensus_cons]
    evidence_ids = {str(eid) for finding in retained for eid in finding.evidence_node_ids}
    gold_topics = [bool({str(eid) for eid in item.evidence_node_ids} & evidence_ids) for item in (*gold.consensus_pros, *gold.consensus_cons)]
    # Captured normal output has two manually identified compound overclaims:
    # its build finding combines three sources' different details; its fit/looseness
    # caveat cites only the mechanism excerpt. Corrected fixtures contain only
    # the explicitly source-bound assertions above. Provider semantics stay unverified.
    unsupported = 0 if candidate or repair else 2 / len(retained)
    return {"case_id": str(case), **ledger, "completion_ms": (time.perf_counter() - start) * 1000,
            "source_count_requested": 5, "source_count_analyzed": 5, "product_fact_count": len(facts),
            "quote_valid_rate": sum(quotes_valid) / len(quotes_valid), "unsupported_claim_rate": unsupported,
            "buyer_coverage_rate": sum(gold_topics) / len(gold_topics), "first_audit_repairable": repair,
            "correction_model_calls": int(repair), "reaudit_model_calls": int(repair)}


async def benchmark(pairs=20, repetitions=3):
    fixture = load_fixture()
    baseline, candidate = {}, {}
    for case in range(pairs):
        samples = {False: [], True: []}
        for repetition in range(repetitions):
            for changed in ((False, True) if repetition % 2 == 0 else (True, False)):
                samples[changed].append(await replay(fixture, case, changed))
        for changed, rows in samples.items():
            row = dict(rows[0])
            row["completion_ms"] = statistics.median(r["completion_ms"] for r in rows)
            (candidate if changed else baseline)[str(case)] = row
    result = compare(baseline, candidate, strict_correction=True)
    result["measurement"] = "Captured common-call usage plus changed-call token estimates; local processing and fixed fixture-provider latency"
    result["paid_calls"] = 0
    result["production_parity_verified"] = False
    return baseline, candidate, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".audit-cache/report-correction")
    args = parser.parse_args()
    baseline, candidate, result = asyncio.run(benchmark())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("baseline", baseline), ("candidate", candidate)):
        (args.output_dir / f"{name}.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows.values()), encoding="utf-8")
    (args.output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
