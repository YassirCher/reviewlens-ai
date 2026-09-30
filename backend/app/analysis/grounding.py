"""Deterministic report grounding and conservative publication cleanup."""

from __future__ import annotations

import re
from typing import Any

from app.analysis.contracts import AuditIssue, AuditResult, ConsensusItem, FinalReportDraft


_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_MODEL_CODE = re.compile(r"\b[a-z][a-z0-9-]*\d[a-z0-9-]*\b", re.I)
_GROUNDING_CODES = {
    "unsupported_claim", "unsupported_finding", "unsupported_disagreement",
    "unsupported_narrative", "numeric_mismatch", "negation_mismatch",
    "scope_mismatch", "missing_central_evidence",
}


def _field_index(path: str, field: str) -> int | None:
    match = re.search(rf"(?:^|\.){field}\[(\d+)\]", path)
    return int(match[1]) if match else None


def _statement_matches(statement: str, claim: str, excerpt: str, product_name: str) -> bool:
    numbers = {item.replace(",", "") for item in _NUMBER.findall(statement)}
    numbers -= {item.replace(",", "") for item in _NUMBER.findall(product_name)}
    if not numbers <= {item.replace(",", "") for item in _NUMBER.findall(excerpt)}:
        return False
    # Polarity is semantic: 'no lag' supports a positive latency observation.
    # The existing auditor checks negation, conditions, and meaning per clause.
    scoped = set(_MODEL_CODE.findall(statement.casefold())) - set(_MODEL_CODE.findall(product_name.casefold()))
    support_codes = set(_MODEL_CODE.findall(excerpt.casefold()))
    if not scoped <= support_codes:
        return False
    return True


def _safe_summary(draft: FinalReportDraft, count: int) -> str:
    pieces = [f"This report analyzed {count} cited review{'s' if count != 1 else ''}."]
    if draft.consensus_pros:
        pieces.append(f"A supported strength is: {draft.consensus_pros[0].statement}")
    if draft.consensus_cons:
        pieces.append(f"A supported caveat is: {draft.consensus_cons[0].statement}")
    if draft.disagreements:
        pieces.append("Reviewers disagree on a purchase-relevant point; inspect both views below.")
    return " ".join(pieces)


def ground_report(
    draft: FinalReportDraft,
    reviews: list[dict[str, Any]],
    model_audit: AuditResult,
    *,
    strict_grounding: bool = False,
) -> tuple[FinalReportDraft, AuditResult, bool]:
    """Remove unsupported material, retain non-grounding audit failures."""
    evidence: dict[str, tuple[str, str, str, str]] = {}
    channels = {str(review["source_id"]): str(review["channel_id"]) for review in reviews}
    central = False
    for review in reviews:
        for claim in review["claims"]:
            for ref in claim["evidence"]:
                evidence[str(ref["evidence_node_id"])] = (
                    str(review["source_id"]), str(claim["claim"]),
                    str(ref["evidence_text"]), str(ref["support_type"]),
                )
                central |= bool(claim["central"] and ref["support_type"] == "supports")

    removed: list[AuditIssue] = []

    def supported(item: ConsensusItem, field: str, index: int) -> bool:
        source_ids = [str(source_id) for source_id in item.source_ids]
        if len(set(source_ids)) != len(source_ids) or not set(source_ids) <= channels.keys():
            code = "finding_source_mismatch"
        elif not item.evidence_node_ids:
            code = "finding_evidence_missing"
        else:
            refs = [evidence.get(str(eid)) for eid in item.evidence_node_ids]
            if any(ref is None for ref in refs):
                code = "finding_evidence_missing"
            elif any(
                not source_supports(source_id, item.statement, refs) for source_id in source_ids
            ):
                code = "finding_support_mismatch"
            else:
                return True
        removed.append(AuditIssue(code=code, field_path=f"report_draft.{field}[{index}]", retryable=False))
        return False

    def source_supports(source_id: str, statement: str,
                        refs: list[tuple[str, str, str, str] | None]) -> bool:
        supporting = [ref for ref in refs if ref is not None and ref[0] == source_id and ref[3] == "supports"]
        # Combine a source's cited excerpts, without borrowing support from
        # another source. Semantic support remains the auditor's responsibility.
        return bool(supporting) and _statement_matches(
            statement, " ".join(dict.fromkeys(ref[1] for ref in supporting)),
            " ".join(ref[2] for ref in supporting), draft.product_canonical_name,
        )

    pros = tuple(item for i, item in enumerate(draft.consensus_pros) if supported(item, "consensus_pros", i))
    cons = tuple(item for i, item in enumerate(draft.consensus_cons) if supported(item, "consensus_cons", i))
    valid_source_ids = set(channels)
    disagreements = []
    for index, item in enumerate(draft.disagreements):
        side_a = {str(source_id) for source_id in item.side_a_source_ids}
        side_b = {str(source_id) for source_id in item.side_b_source_ids}
        if side_a and side_b and not side_a & side_b and side_a | side_b <= valid_source_ids:
            disagreements.append(item)
        else:
            removed.append(AuditIssue(
                code="disagreement_source_mismatch",
                field_path=f"report_draft.disagreements[{index}]", retryable=False,
            ))

    summary_flagged = False
    buy = list(draft.who_should_buy)
    avoid = list(draft.who_should_avoid)
    excluded_buy: set[int] = set()
    excluded_avoid: set[int] = set()
    clear_buy = False
    clear_avoid = False
    unhandled: list[AuditIssue] = []
    for issue in model_audit.issues:
        if issue.code not in _GROUNDING_CODES:
            unhandled.append(issue)
            continue
        path = issue.field_path.removeprefix("report_draft.")
        if path.startswith("consensus_pros"):
            target_index = _field_index(path, "consensus_pros")
            if target_index is None:
                pros = ()
            elif target_index < len(draft.consensus_pros):
                pros = tuple(item for item in pros if item != draft.consensus_pros[target_index])
        elif path.startswith("consensus_cons"):
            target_index = _field_index(path, "consensus_cons")
            if target_index is None:
                cons = ()
            elif target_index < len(draft.consensus_cons):
                cons = tuple(item for item in cons if item != draft.consensus_cons[target_index])
        elif path.startswith("disagreements"):
            target_index = _field_index(path, "disagreements")
            if target_index is None:
                disagreements = []
            elif target_index < len(draft.disagreements):
                disagreements = [item for item in disagreements if item != draft.disagreements[target_index]]
        elif path.startswith("summary"):
            summary_flagged = True
        elif path.startswith("who_should_buy"):
            target_index = _field_index(path, "who_should_buy")
            if target_index is None:
                clear_buy = True
            else:
                excluded_buy.add(target_index)
        elif path.startswith("who_should_avoid"):
            target_index = _field_index(path, "who_should_avoid")
            if target_index is None:
                clear_avoid = True
            else:
                excluded_avoid.add(target_index)
        else:
            unhandled.append(issue)
            continue
        removed.append(issue.model_copy(update={"retryable": False}))

    buy = [] if clear_buy else [item for index, item in enumerate(buy) if index not in excluded_buy]
    avoid = [] if clear_avoid else [item for index, item in enumerate(avoid) if index not in excluded_avoid]

    safe = draft.model_copy(update={
        "consensus_pros": pros,
        "consensus_cons": cons,
        "disagreements": tuple(disagreements),
        "who_should_buy": tuple(buy) if len(pros) == len(draft.consensus_pros) else (),
        "who_should_avoid": tuple(avoid) if len(cons) == len(draft.consensus_cons) else (),
    })
    usage_source = next(
        (review for review in reviews if str(review["source_id"]) == str(safe.longest_usage_source_id)),
        None,
    )
    if safe.longest_usage_period and (not usage_source or not usage_source.get("usage_period_mentioned")):
        safe = safe.model_copy(update={"longest_usage_period": None, "longest_usage_source_id": None})
        removed.append(AuditIssue(code="usage_period_unverified", field_path="report_draft.longest_usage_period", retryable=False))
    if removed or summary_flagged:
        safe = safe.model_copy(update={"summary": _safe_summary(safe, len(reviews))})
    if not central or not pros and not cons:
        unhandled.append(AuditIssue(code="grounded_conclusion_missing", field_path="report_draft", retryable=False))
    issues = tuple({(item.code, item.field_path): item for item in (*removed, *unhandled)}.values())
    fatal = [item for item in unhandled if item.code == "grounded_conclusion_missing"]
    if model_audit.verdict == "fail":
        fatal.extend(item for item in unhandled if item.code != "grounded_conclusion_missing")
    if fatal:
        # An empty safe draft can be rebuilt from validated central evidence in
        # the one existing correction stage. Unknown strict-audit failures and
        # missing central evidence cannot be repaired by spending another call.
        repairable_codes = _GROUNDING_CODES | {"grounded_conclusion_missing", "summary_scope_requires_correction"}
        terminal = (
            not central
            or any(
                item.code not in repairable_codes and (strict_grounding or not item.retryable)
                for item in fatal
            )
        )
        return safe, AuditResult(verdict="fail", issues=issues), terminal
    if issues or model_audit.verdict == "pass_with_warnings":
        return safe, AuditResult(verdict="pass_with_warnings", issues=issues), False
    return safe, AuditResult(verdict="pass", issues=()), False
