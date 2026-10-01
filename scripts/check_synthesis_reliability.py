"""Matched captured-input/offline replays. Human fixtures do not prove live model accuracy."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.analysis.audit import (
    DecisionAuditorInput,
    decision_audit_input,
    finding_audit_schema,
)
from app.analysis.audit_parts import PartAuditorInput, part_audit_input, referenced_audit_schema
from app.analysis.contracts import AuditResult
from app.analysis.grounding import ground_report
from app.analysis.prompting import build_prompt_envelope
from app.analysis.registry import AGENT_REGISTRY
from app.analysis.rendering import (
    DistinctBuyingSynthesis,
    prioritized_synthesis_input,
)
from app.analysis.review import ClassifiedVideoExtraction, VideoExtraction
from app.analysis.synthesis import (
    EvidenceBoundBuyingSynthesis,
    catalog_repair_synthesis_input,
    evidence_bound_synthesis_schema,
    evidence_catalog,
)
from app.knowledge.retrieval import estimate_tokens


def baseline_module(name, path):
    module = types.ModuleType(name)
    sys.modules[name] = module
    result = subprocess.run(["git", "show", "7a3aa9c:" + path], cwd=ROOT, check=True, capture_output=True, text=True)
    exec(compile(result.stdout, path, "exec"), module.__dict__)  # noqa: S102 -- pinned local baseline, no untrusted input
    return module


def main():
    capture = json.loads((ROOT / "backend/tests/fixtures/live_synthesis_reliability.json").read_text(encoding="utf-8-sig"))
    old_registry = baseline_module("reliability_old_registry", "backend/app/analysis/registry.py")
    old_ground = baseline_module("reliability_old_ground", "backend/app/analysis/grounding.py")
    rows = []
    for case in range(24):
        run = capture["runs"][case % 2]
        reviews = copy.deepcopy(run["source_analyses"])
        catalog, bindings, _sources = evidence_catalog(reviews)
        quote = next(q for q in catalog["evidence_catalog"] if len(q["excerpt"]) < 100 and q["support_type"] == "supports")
        owner_ref = quote["source_ref"]
        assertion = {"kind": "strength", "attribute": "Reviewer observation", "observation": quote["excerpt"],
                     "conditions": None, "evidence_refs": [quote["evidence_ref"]]}
        output = {"summary": "Cited reviewer observations.", "assertions": [assertion]}
        raw = {"product_display_name": run["product"], "product_canonical_name": run["product"],
               "requested_source_count": 5, "source_analyses": reviews, "audience_analyses": run["audience_analyses"]}
        mode = case // 2
        if mode == 0: assertion["observation"] = owner_ref + " states: " + quote["excerpt"]
        elif mode == 1: assertion["conditions"] = "(" + quote["evidence_ref"] + ")"
        elif mode == 2: assertion["attribute"] = owner_ref + " observation"
        elif mode == 3: assertion["observation"] += " [" + quote["evidence_ref"] + "]"
        elif mode == 4: assertion["evidence_refs"] *= 2
        elif mode == 5:
            output["summary"] = owner_ref + " describes the cited observation"
        elif mode == 6:
            raw["product_display_name"] = raw["product_canonical_name"] = "Example E1"
            assertion["attribute"] = "E1 observation"
        elif mode == 7:
            # One ambiguous presentation finding, another safe finding: no report-wide failure.
            output["assertions"].append(copy.deepcopy(assertion))
            assertion["observation"] = "s99 reports a benefit"
        elif mode == 8:
            # The entire draft needs the existing bounded correction, not a new allowance.
            assertion["observation"] = "s99 reports a benefit"
        elif mode == 9:
            assertion["observation"] = "Default aperture is f/1.8"
            source = next(r for r in reviews if r["source_id"] == bindings[quote["evidence_ref"]][0])
            evidence = next(e for c in source["claims"] for e in c["evidence"] if e["evidence_node_id"] == bindings[quote["evidence_ref"]][1])
            evidence["evidence_text"] = "Default aperture is f1.8"
            quote["excerpt"] = evidence["evidence_text"]
        elif mode == 10:
            # Human negative: a unit mismatch cannot become a published benefit.
            assertion["observation"] = "Weight is 18 g"
            output["assertions"].append({**copy.deepcopy(assertion), "observation": quote["excerpt"]})
        elif mode == 11:
            # Failed final audit stays unpublished in both versions.
            output["assertions"][0]["observation"] = quote["excerpt"]

        pair = {}
        for changed in (False, True):
            started = time.perf_counter()
            registry = AGENT_REGISTRY if changed else old_registry.AGENT_REGISTRY
            spec = registry["consensus_analyst"]
            supplied = (prioritized_synthesis_input(raw) if changed else catalog_repair_synthesis_input(raw))
            validated = spec.input_model.model_validate(supplied)
            envelope = build_prompt_envelope(spec, task_instruction=spec.purpose, task_input=validated.model_dump(mode="json"),
                                             context_manifest_id=None, rendered_context="<no-authorized-context />")
            schema = evidence_bound_synthesis_schema(validated)
            synthesis_cost = estimate_tokens(envelope.system) + estimate_tokens(envelope.user) + estimate_tokens(json.dumps(schema)) + estimate_tokens(json.dumps(output))
            # Captured common calls plus ALL estimated successor review prompt/schema/metadata changes.
            common = sum(u["tokens"] for u in run["usage"] if not u["call"].startswith(("build_consensus", "audit_report")))
            if changed:
                old_spec = old_registry.AGENT_REGISTRY["review_analyst"]
                new_spec = registry["review_analyst"]
                delta = estimate_tokens(new_spec.persisted_payload()["system_prompt"]) - estimate_tokens(old_spec.persisted_payload()["system_prompt"])
                delta += estimate_tokens(json.dumps(ClassifiedVideoExtraction.model_json_schema())) - estimate_tokens(json.dumps(VideoExtraction.model_json_schema()))
                # Charge the maximum successor metadata length for EVERY claim;
                # do not assume the model will emit short topics.
                common += 5 * delta + sum(estimate_tokens(json.dumps({"kind": "strength", "topic": "x" * 40})) for r in reviews for _ in r["claims"])
            costs, calls, repairs, reaudits = common + synthesis_cost, 1, 0, 0
            draft = None
            try:
                model = (DistinctBuyingSynthesis if changed else EvidenceBoundBuyingSynthesis).model_validate(output)
                draft = model.as_report(raw["product_display_name"], raw["product_canonical_name"], reviews)
            except ValueError:
                # One unsuccessful validation retry, counted in full.
                costs += synthesis_cost; calls += 1
            if changed and draft is not None and not draft.consensus_pros and not draft.consensus_cons:
                repairs = 1
                repaired = {"summary": "Cited reviewer observations.", "assertions": [{**assertion, "observation": quote["excerpt"]}]}
                # Include the original input plus the bounded rejected draft and target, conservatively.
                costs += synthesis_cost + estimate_tokens(json.dumps(repaired)) + 200; calls += 1
                draft = DistinctBuyingSynthesis.model_validate(repaired).as_report(raw["product_display_name"], raw["product_canonical_name"], reviews)
                reaudits = 1
            retained = []
            published = False
            if draft is not None:
                audit_spec = registry["quality_auditor"]
                audit_raw = {"report_draft": draft.model_dump(mode="json"), "source_analyses": reviews}
                audit_input = (PartAuditorInput.model_validate(part_audit_input(audit_raw)) if changed else
                               DecisionAuditorInput.model_validate(decision_audit_input(audit_raw)))
                audit_envelope = build_prompt_envelope(audit_spec, task_instruction=audit_spec.purpose,
                    task_input=audit_input.model_dump(mode="json"), context_manifest_id=None, rendered_context="<no-authorized-context />")
                audit_schema = referenced_audit_schema(audit_input) if changed else finding_audit_schema(audit_input)
                costs += estimate_tokens(audit_envelope.system) + estimate_tokens(audit_envelope.user) + estimate_tokens(json.dumps(audit_schema)) + 200
                calls += 1
                issues = () if mode != 11 else tuple({"code": "unsupported_finding", "field_path": f"report_draft.consensus_pros[{i}]"} for i in range(len(draft.consensus_pros)))
                audit = AuditResult.model_validate({"verdict": "fail" if issues else "pass", "issues": issues})
                safe, verdict, _ = (ground_report if changed else old_ground.ground_report)(draft, reviews, audit, strict_grounding=True)
                retained = [*safe.consensus_pros, *safe.consensus_cons]
                published = verdict.verdict != "fail"
            # Negative fixture decisions are independently prescribed, not inferred from success.
            assert mode != 11 or not published
            if changed and mode == 10:
                assert all("Weight is 18 g" not in f.statement for f in retained)
            if changed and mode == 9:
                assert published and retained
            base_calls = 7 + (5 if run["audience_analyses"] else 0)
            unsupported = sum("Weight is 18 g" in f.statement for f in retained) if mode == 10 else 0
            pair["candidate" if changed else "baseline"] = {"tokens": costs, "calls": base_calls + calls,
                "retained": len(retained), "valid_retained": len(retained) - unsupported,
                "unsupported_retained": unsupported, "published": published, "local_ms": (time.perf_counter()-started)*1000,
                "correction_calls": repairs, "reaudit_calls": reaudits, "source_coverage": len(reviews)}
        assert pair["candidate"]["source_coverage"] == pair["baseline"]["source_coverage"] == 5
        assert pair["candidate"]["valid_retained"] >= pair["baseline"]["valid_retained"]
        assert pair["candidate"]["unsupported_retained"] <= pair["baseline"]["unsupported_retained"]
        assert pair["candidate"]["correction_calls"] <= 1 and pair["candidate"]["reaudit_calls"] <= 1
        rows.append({"case": case, "mode": mode, "comments": bool(run["audience_analyses"]), **pair})
    old_tokens = sum(r["baseline"]["tokens"] for r in rows)
    new_tokens = sum(r["candidate"]["tokens"] for r in rows)
    result = {"passed": new_tokens <= old_tokens, "matched_cases": len(rows), "paid_calls": 0,
        "baseline_estimated_mean_tokens": old_tokens/len(rows), "candidate_estimated_mean_tokens": new_tokens/len(rows),
        "production_parity_verified": False, "live_accuracy_verified": False,
        "local_p95_ms": {k: sorted(r[k]["local_ms"] for r in rows)[-2] for k in ("baseline", "candidate")},
        "measurement": "Captured common-call usage plus complete changed-envelope/schema/output estimates; local processing only",
        "cases": rows}
    output = ROOT / ".audit-cache/synthesis-reliability-replays.json"
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != "cases"}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
