"""Deterministic report grounding and conservative publication cleanup."""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

from app.analysis.contracts import AuditIssue, AuditResult, ConsensusItem, FinalReportDraft
from app.analysis.quantities import decimal, explicit_quantities, without_product_identity, product_passage, normalize_quantity_words


_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_MIXED_FRACTION = re.compile(r"(?<![\w.])(\d+)\s+(?:and\s+)?(\d+)\s*/\s*(\d+)(?![\w.])", re.I)
_MODEL_CODE = re.compile(r"\b[a-z][a-z0-9-]*\d[a-z0-9-]*\b", re.I)
_DURATION = re.compile(
    r"(?<![\w.])(?P<amount>\d+\s+(?:and\s+)?\d+\s*/\s*\d+|\d+(?:[.,]\d+)?|(?P<verbal>half(?:\s+an?)?|an?|one))"
    r"(?(verbal)[\s-]+|[\s-]*)(?P<unit>milliseconds?|msecs?|ms|microseconds?|usecs?|us|µs|μs|hours?|hrs?|h|minutes?|mins?|seconds?|secs?|s)\b", re.I,
)
_GROUNDING_CODES = {
    "unsupported_claim", "unsupported_finding", "unsupported_disagreement",
    "unsupported_narrative", "numeric_mismatch", "negation_mismatch",
    "scope_mismatch", "missing_central_evidence",
}

_MEASUREMENT_TOPIC = re.compile(
    r"\b(?P<noise>noise[ -]cancell\w*|anc|isolation)|\b(?P<sound>sound(?:\s+quality)?|audio(?:\s+quality)?|frequency\s+response)|"
    r"\b(?P<battery>battery(?:\s+life)?|endurance)|\b(?P<charging>charg\w*\s+(?:speed|time|rate))|"
    r"\b(?P<weight>weigh\w*|mass)", re.I)


def incomplete_prose(text: str) -> bool:
    """Recognize dangling English fragments without requiring punctuation on labels."""
    _, text = explicit_quantities(text)
    return bool(re.search(r"(?:\s+[b-df-hj-np-tv-z]|\b(?:the|a|an|and|or|because|which|to|with|they['’](?:ve|re))|"
                          r"\b(?:days? with heavy|before (?:one|both) of (?:my|the) \w+|the case you|(?:for|about)\s+\d+))$",
                          text.strip(), re.I))


def _semantic_edges(statement: str, excerpt: str) -> list[str]:
    """Explicit missing qualifications/predicates; the auditor still judges meaning."""
    issues = []
    for term, support in (
        (r"\bcareful(?:ly)?\b", r"\bcareful(?:ly)?\b|सावधान|संभाल|précaution"),
        (r"\bheavy (?:use|usage)\b", r"\bheavy (?:use|usage)\b|हैवी यूसेज"),
        (r"\b(?:ship|shipped)\b", r"\b(?:ship|shipped|shipping|delivered|released)\b"),
        (r"\bear(?:s)? touch(?:es)?\b", r"\b(?:touch\w*|press\w*|contact)\b"),
        (r"\b(?:case is (?:larger|large)|case.*larger than)\b", r"\b(?:larger|large|bigger|big|huge)\b"),
        (r"\b(?:more visible|visibility)\b", r"\b(?:visible|visibility|noticeable)\b|विजिबल|दिखाई|दिखते"),
    ):
        if re.search(term, statement, re.I) and not re.search(support, excerpt, re.I):
            issues.append("qualification_or_predicate_not_cited")
    for property_name in re.findall(r"\b(?:Snapdragon\s+Sound|LC3|LDAC|USB-C\s+audio|360\s+Reality\s+Audio|ChatGPT)\b", statement, re.I):
        if not re.search(re.escape(property_name).replace(r"\ ", r"\s+"), excerpt, re.I):
            issues.append("named_property_not_cited")
    return sorted(set(issues))


def _measurement_topics(text: str) -> dict[Fraction, set[str]]:
    active: str | None = None
    result: dict[Fraction, set[str]] = {}
    tokens = sorted([(match.start(), "topic", match.lastgroup) for match in _MEASUREMENT_TOPIC.finditer(text)] +
                    [(match.start(), "number", match[0]) for match in _NUMBER.finditer(text)])
    for _, kind, value in tokens:
        if kind == "topic":
            active = value
        elif active:
            result.setdefault(decimal(value), set()).add(active)
    return result


def _field_index(path: str, field: str) -> int | None:
    match = re.search(rf"(?:^|\.){field}\[(\d+)\]", path)
    return int(match[1]) if match else None


def _quantities(text: str) -> set[Fraction]:
    values: set[Fraction] = set()

    def mixed(match: re.Match[str]) -> str:
        whole, numerator, denominator = (int(value) for value in match.groups())
        if 0 < numerator < denominator:
            values.add(Fraction(whole) + Fraction(numerator, denominator))
            return ""
        return match[0]

    remaining = _MIXED_FRACTION.sub(mixed, text)
    values.update(decimal(item) for item in _NUMBER.findall(remaining))
    return values


def _statement_matches(statement: str, claim: str, excerpt: str, product_name: str) -> bool:
    return not statement_mismatches(statement, excerpt, product_name)


def statement_mismatches(statement: str, excerpt: str, product_name: str) -> list[str]:
    # Reviewers use plural/separated model codes. Normalize only explicit codes;
    # phrases such as 'last year' never establish a sibling identity.
    if re.search(r"\bWH[ -]?1000XM\d\b", product_name, re.I) and not re.search(r"\bWF[ -]?1000XM\d", statement + ' ' + excerpt, re.I):
        def alias(text: str) -> str:
            text = re.sub(r"\bWH[ -]?1000XM(\d)s?\b", r"WH1000XM\1", text, flags=re.I)
            return re.sub(r"\b(?:XM[ -]?|mark[ -]?)(\d)(?:[ -]?s)?\b", r"WH1000XM\1", text, flags=re.I)
        statement, excerpt, product_name = alias(statement), alias(excerpt), alias(product_name)
    # Explicit comparisons still need both products/results. Single-product
    # quantities cannot borrow a sibling measurement from a mixed quotation.
    extra_codes = set(_MODEL_CODE.findall(statement.casefold())) - set(_MODEL_CODE.findall(product_name.casefold()))
    if not extra_codes and not re.search(r"\b(?:compared|comparison|versus|vs\.?|upgrad\w*)\b", statement, re.I):
        excerpt = product_passage(excerpt, product_name)
    statement = without_product_identity(statement, product_name)
    issues = ["incomplete_prose"] if incomplete_prose(statement) else []
    issues.extend(_semantic_edges(statement, excerpt))
    quoted_topics = _measurement_topics(without_product_identity(excerpt, product_name))
    if any(value in quoted_topics and not topics <= quoted_topics[value]
           for value, topics in _measurement_topics(statement).items()):
        issues.append("measurement_subject_mismatch")
    if re.search(r"\bmost comfortable\b", statement, re.I) and not re.search(
            r"\bmost comfortable\b|\bcomfortable\b.{0,50}\bever\b", excerpt, re.I):
        issues.append("superlative_not_cited")
    statement_times, remaining_statement = _durations(statement)
    excerpt_times, remaining_excerpt = _durations(excerpt)
    if not statement_times <= excerpt_times:
        issues.append("duration_not_cited")
    measured, plain_statement = explicit_quantities(remaining_statement)
    quoted, plain_excerpt = explicit_quantities(remaining_excerpt)
    if not measured <= quoted:
        issues.append("quantity_or_unit_not_cited")
    # Plain numeric prose can cite a measured value, but an explicit unit must match.
    quoted_numbers = _quantities(plain_excerpt) | {value for _, value in quoted}
    if not _quantities(plain_statement) <= quoted_numbers:
        issues.append("number_not_cited")
    # Polarity is semantic: 'no lag' supports a positive latency observation.
    # The existing auditor checks negation, conditions, and meaning per clause.
    scoped = set(_MODEL_CODE.findall(plain_statement.casefold())) - set(_MODEL_CODE.findall(product_name.casefold()))
    support_codes = set(_MODEL_CODE.findall(plain_excerpt.casefold()))
    if not scoped <= support_codes:
        issues.append("model_code_not_cited")
    return issues


def _durations(text: str) -> tuple[set[Fraction], str]:
    """Normalize explicit time quantities to minutes, independently of percentages."""
    values: set[Fraction] = set()

    def replace(match: re.Match[str]) -> str:
        raw = match["amount"].casefold()
        mixed = _MIXED_FRACTION.fullmatch(raw)
        if mixed:
            whole, numerator, denominator = (int(value) for value in mixed.groups())
            if not 0 < numerator < denominator:
                return match[0]
            amount = Fraction(whole) + Fraction(numerator, denominator)
        else:
            amount = Fraction(1, 2) if raw.startswith("half") else (
                Fraction(1) if raw in {"a", "an", "one"} else decimal(raw))
        unit = match["unit"].casefold()
        multiplier = (Fraction(1, 60000) if unit.startswith(("millisecond", "msec")) or unit == "ms" else
                      Fraction(1, 60000000) if unit.startswith(("microsecond", "usec")) or unit in {"us", "µs", "μs"} else
                      Fraction(60) if unit.startswith(("h", "hr")) else
                      Fraction(1, 60) if unit.startswith("s") else Fraction(1))
        values.add(amount * multiplier)
        return ""

    return values, _DURATION.sub(replace, normalize_quantity_words(text))


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
    diagnostics: list[dict[str, Any]] | None = None,
    owned_guidance: bool = False,
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
        if diagnostics is not None and len(diagnostics) < 96:
            checks = []
            for source_id in source_ids:
                owned = [evidence.get(str(eid)) for eid in item.evidence_node_ids]
                excerpts = " ".join(ref[2] for ref in owned if ref and ref[0] == source_id and ref[3] == "supports")
                checks.extend(statement_mismatches(item.statement, excerpts, draft.product_canonical_name))
            diagnostics.append({"field_path": f"report_draft.{field}[{index}]", "code": code,
                                "reasons": sorted(set(checks))})
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
        # Providers can vary casing even under the strict string contract.
        # Canonicalize only known codes; unknown failures still block publication.
        canonical_code = issue.code.casefold()
        if canonical_code in _GROUNDING_CODES:
            issue = issue.model_copy(update={"code": canonical_code})
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
    if owned_guidance:
        safe = finding_narrative(safe, len(reviews))
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


def finding_narrative(draft: FinalReportDraft, count: int) -> FinalReportDraft:
    """Templates reuse surviving attributed assertions; they introduce no facts."""
    return draft.model_copy(update={"summary": _safe_summary(draft, count),
        "who_should_buy": tuple(f"Consider whether this matters to you: {item.statement}" for item in draft.consensus_pros[:3]),
        "who_should_avoid": tuple(f"Consider this caveat before buying: {item.statement}" for item in draft.consensus_cons[:3])})
