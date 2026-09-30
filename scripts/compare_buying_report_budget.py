"""Gate a staged buying-report change against paired fixture run metrics.

Each JSONL row must contain case_id, total_tokens, model_call_count,
completion_ms, quote_valid_rate, unsupported_claim_rate, and buyer_coverage_rate.
Candidate rows with a repairable failed first audit may also record
first_audit_repairable=true, correction_model_calls, and reaudit_model_calls.
Run the same fixtures, selected sources, model policy, and concurrency before and
after activation. This script makes no provider calls.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

METRICS = (
    "total_tokens", "model_call_count", "completion_ms", "quote_valid_rate",
    "unsupported_claim_rate", "buyer_coverage_rate",
)


def repair_calls(row: dict) -> int:
    eligible = row.get("first_audit_repairable", False)
    correction = row.get("correction_model_calls", 0)
    reaudit = row.get("reaudit_model_calls", 0)
    if not isinstance(eligible, bool):
        raise ValueError("first_audit_repairable must be a boolean")  # noqa: TRY004 -- invalid metric data uses the gate's ValueError contract
    if type(correction) is not int or correction not in (0, 1):
        raise ValueError("correction_model_calls must be zero or one")
    if type(reaudit) is not int or reaudit not in (0, 1):
        raise ValueError("reaudit_model_calls must be zero or one")
    if (correction or reaudit) and not eligible:
        raise ValueError("repair calls require a repairable failed first audit")
    if reaudit > correction:
        raise ValueError("reaudit requires a correction call")
    return correction + reaudit


def load_rows(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = str(row.get("case_id") or "")
        if not case_id or case_id in rows:
            raise ValueError(f"{path}:{number}: missing or duplicate case_id")
        for key in METRICS:
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{path}:{number}: invalid {key}")
        for key in ("quote_valid_rate", "unsupported_claim_rate", "buyer_coverage_rate"):
            if row[key] > 1:
                raise ValueError(f"{path}:{number}: {key} must be at most 1")
        try:
            repair_calls(row)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        rows[case_id] = row
    return rows


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def compare(baseline: dict[str, dict], candidate: dict[str, dict], min_pairs: int = 20,
            strict_coverage: bool = False, strict_correction: bool = False, strict_audit: bool = False) -> dict:
    if strict_coverage or strict_correction or strict_audit:
        min_pairs = max(20, min_pairs)
    if baseline.keys() != candidate.keys() or len(baseline) < min_pairs:
        raise ValueError(f"identical case IDs and at least {min_pairs} paired runs are required")
    base = list(baseline.values())
    changed = [candidate[case_id] for case_id in baseline]
    repairs = {key: repair_calls(candidate[key]) for key in baseline}
    normal_keys = list(baseline) if strict_coverage or strict_correction or strict_audit else [key for key, calls in repairs.items() if calls == 0]
    normal_base = [baseline[key] for key in normal_keys]
    normal_changed = [candidate[key] for key in normal_keys]

    def mean(rows: list[dict], key: str) -> float:
        return sum(float(row[key]) for row in rows) / len(rows)

    checks = {
        "model_calls": all(candidate[key]["model_call_count"] <= baseline[key]["model_call_count"] + repairs[key]
                           for key in baseline),
        "mean_tokens": not normal_keys or mean(normal_changed, "total_tokens") <= mean(normal_base, "total_tokens"),
        "p95_tokens": not normal_keys or percentile([row["total_tokens"] for row in normal_changed], .95) <= percentile([row["total_tokens"] for row in normal_base], .95),
        "p95_latency": not normal_keys or percentile([row["completion_ms"] for row in normal_changed], .95) <= percentile([row["completion_ms"] for row in normal_base], .95),
        "quote_validity": mean(changed, "quote_valid_rate") >= mean(base, "quote_valid_rate"),
        "unsupported_claims": mean(changed, "unsupported_claim_rate") <= mean(base, "unsupported_claim_rate"),
        "buyer_coverage": mean(changed, "buyer_coverage_rate") >= mean(base, "buyer_coverage_rate"),
    }
    if strict_coverage or strict_correction or strict_audit:
        for row in [*base, *changed]:
            requested, analyzed = row.get("source_count_requested"), row.get("source_count_analyzed")
            if type(requested) is not int or type(analyzed) is not int or not 1 <= analyzed <= requested <= 8:
                raise ValueError("strict coverage requires valid requested and analyzed source counts")
        checks["source_coverage"] = all(
            candidate[key]["source_count_requested"] == baseline[key]["source_count_requested"]
            and candidate[key]["source_count_analyzed"] >= baseline[key]["source_count_analyzed"]
            for key in baseline
        ) and (strict_correction or strict_audit or sum(row["source_count_analyzed"] for row in changed) > sum(row["source_count_analyzed"] for row in base))
    if strict_audit:
        checks["normal_calls"] = all(candidate[key]["model_call_count"] - repairs[key]
                                    <= candidate[key]["source_count_analyzed"] + 4 for key in baseline)
    if strict_correction:
        for row in [*base, *changed]:
            if type(row.get("product_fact_count")) is not int or row["product_fact_count"] < 0:
                raise ValueError("strict correction requires valid product fact counts")
        checks["product_facts"] = all(candidate[key]["product_fact_count"] >= baseline[key]["product_fact_count"]
                                      for key in baseline) and sum(row["product_fact_count"] for row in changed) > sum(row["product_fact_count"] for row in base)
        checks["normal_calls"] = all(candidate[key]["model_call_count"] - repairs[key]
                                    <= candidate[key]["source_count_analyzed"] + 4 for key in baseline)
    return {
        "passed": all(checks.values()), "paired_runs": len(base),
        "repair_cases": sum(bool(calls) for calls in repairs.values()), "checks": checks,
        "baseline": {"mean_tokens": mean(base, "total_tokens"),
                     "p95_tokens": percentile([row["total_tokens"] for row in base], .95),
                     "p95_latency_ms": percentile([row["completion_ms"] for row in base], .95)},
        "candidate": {"mean_tokens": mean(changed, "total_tokens"),
                      "p95_tokens": percentile([row["total_tokens"] for row in changed], .95),
                      "p95_latency_ms": percentile([row["completion_ms"] for row in changed], .95)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--min-pairs", type=int, default=20)
    parser.add_argument("--strict-coverage", action="store_true",
                        help="Include audit repairs in performance checks and require greater valid source coverage")
    parser.add_argument("--strict-correction", action="store_true",
                        help="Include all repairs, preserve coverage, improve fact retention and enforce nine normal calls for five sources")
    parser.add_argument("--strict-audit", action="store_true",
                        help="Include all repairs, preserve source coverage and enforce existing normal-call limits")
    args = parser.parse_args()
    try:
        result = compare(load_rows(args.baseline), load_rows(args.candidate), args.min_pairs,
                         args.strict_coverage, args.strict_correction, args.strict_audit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
