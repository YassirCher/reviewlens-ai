"""Compact citation audit input with the synthesis catalog's server-bound references."""
from __future__ import annotations

import copy
from typing import Any
from typing import Literal

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from app.analysis.contracts import AuditIssue, AuditResult, StrictModel
from app.analysis.synthesis import CatalogEvidence, CatalogSource, evidence_catalog


class AuditFinding(StrictModel):
    statement: str
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=30)


class AuditDisagreement(StrictModel):
    topic: str
    side_a: str
    side_a_source_refs: tuple[str, ...]
    side_b: str
    side_b_source_refs: tuple[str, ...]


class AuditDraft(StrictModel):
    product_display_name: str
    product_canonical_name: str
    summary: str
    consensus_pros: tuple[AuditFinding, ...]
    consensus_cons: tuple[AuditFinding, ...]
    disagreements: tuple[AuditDisagreement, ...]
    longest_usage_period: str | None
    longest_usage_source_ref: str | None
    who_should_buy: tuple[str, ...]
    who_should_avoid: tuple[str, ...]
    limitations: tuple[str, ...]


class CatalogAuditorInput(StrictModel):
    report_draft: AuditDraft
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)
    evidence_catalog: tuple[CatalogEvidence, ...] = Field(min_length=1, max_length=384)


def compact_audit_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve report indices while replacing UUIDs with one shared evidence catalog."""
    catalog, bindings, sources = evidence_catalog(payload["source_analyses"])
    source_refs = {owner: ref for ref, owner in sources.items()}
    evidence_refs = {eid: ref for ref, (_, eid) in bindings.items()}
    draft = dict(payload["report_draft"])
    for field in ("consensus_pros", "consensus_cons"):
        draft[field] = [{"statement": item["statement"],
                         "source_refs": [source_refs[str(sid)] for sid in item["source_ids"]],
                         "evidence_refs": [evidence_refs[str(eid)] for eid in item["evidence_node_ids"]]}
                        for item in draft.get(field, [])]
    draft["disagreements"] = [
        {"topic": item["topic"], "side_a": item["side_a"], "side_b": item["side_b"],
         "side_a_source_refs": [source_refs[str(sid)] for sid in item["side_a_source_ids"]],
         "side_b_source_refs": [source_refs[str(sid)] for sid in item["side_b_source_ids"]]}
        for item in draft.get("disagreements", [])]
    usage_owner = draft.pop("longest_usage_source_id", None)
    draft["longest_usage_source_ref"] = source_refs.get(str(usage_owner)) if usage_owner else None
    return {"report_draft": draft, **catalog}


class AuditCitation(StrictModel):
    evidence_ref: str
    source_ref: str
    excerpt: str
    support_type: Literal["supports", "contradicts"]


class CitedAuditFinding(StrictModel):
    statement: str
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    citations: tuple[AuditCitation, ...] = Field(min_length=1, max_length=30)


class CitedAuditDisagreement(StrictModel):
    topic: str
    side_a: str
    side_a_source_refs: tuple[str, ...]
    side_a_citations: tuple[AuditCitation, ...]
    side_b: str
    side_b_source_refs: tuple[str, ...]
    side_b_citations: tuple[AuditCitation, ...]


class CitedAuditDraft(StrictModel):
    product_display_name: str
    product_canonical_name: str
    summary: str
    consensus_pros: tuple[CitedAuditFinding, ...]
    consensus_cons: tuple[CitedAuditFinding, ...]
    disagreements: tuple[CitedAuditDisagreement, ...]
    longest_usage_period: str | None
    longest_usage_source_ref: str | None
    who_should_buy: tuple[str, ...]
    who_should_avoid: tuple[str, ...]
    limitations: tuple[str, ...]


class CitedAuditorInput(StrictModel):
    report_draft: CitedAuditDraft
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)


def cited_audit_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Place server-owned quotes beside each finding; omit derived claim prose."""
    compact = compact_audit_input(payload)
    quotes = {item["evidence_ref"]: {key: item[key] for key in AuditCitation.model_fields}
              for item in compact["evidence_catalog"]}
    draft = compact["report_draft"]
    for field in ("consensus_pros", "consensus_cons"):
        for finding in draft[field]:
            finding["citations"] = [quotes[ref] for ref in finding.pop("evidence_refs")]
    # Persisted disagreement sides have source ownership but no evidence UUIDs.
    # Retain their owners' complete verified quote set for independent semantic checks.
    for disagreement in draft["disagreements"]:
        for side in ("a", "b"):
            owners = disagreement[f"side_{side}_source_refs"]
            disagreement[f"side_{side}_citations"] = [quote for quote in quotes.values()
                                                        if quote["source_ref"] in owners]
    return {"report_draft": draft, "sources": compact["sources"]}


class IndexedAuditFinding(CitedAuditFinding):
    field_path: str


class IndexedAuditDraft(CitedAuditDraft):
    consensus_pros: tuple[IndexedAuditFinding, ...]
    consensus_cons: tuple[IndexedAuditFinding, ...]


class DecisionAuditorInput(CitedAuditorInput):
    report_draft: IndexedAuditDraft


def decision_audit_input(payload: dict[str, Any]) -> dict[str, Any]:
    compact = cited_audit_input(payload)
    for field in ("consensus_pros", "consensus_cons"):
        for index, finding in enumerate(compact["report_draft"][field]):
            finding["field_path"] = f"report_draft.{field}[{index}]"
    return compact


class FindingDecision(StrictModel):
    field_path: str = Field(min_length=1, max_length=300)
    supported: bool
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=30)
    category: Literal["material", "quantity", "condition", "attribution", "scope", "polarity", "measurement"] | None
    unsupported_clause: str | None = Field(max_length=200)
    explanation: str | None = Field(max_length=180)

    @model_validator(mode="after")
    def rejection_is_specific(self) -> "FindingDecision":
        if self.supported:
            if any(value is not None for value in (self.category, self.unsupported_clause, self.explanation)):
                raise PydanticCustomError("supported_rejection_fields_must_be_null", "supported finding cannot contain rejection fields")
        elif not self.category or not (self.unsupported_clause or "").strip() or not (self.explanation or "").strip():
            raise PydanticCustomError("rejection_clause_and_explanation_required", "rejected finding requires a category, exact clause, and explanation")
        return self


class ExplainedAuditIssue(StrictModel):
    code: Literal["unsupported_narrative", "unsupported_disagreement"]
    field_path: str = Field(min_length=1, max_length=300)
    evidence_refs: tuple[str, ...] = Field(max_length=30)
    unsupported_clause: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=180)

    @model_validator(mode="after")
    def nonempty_reason(self) -> "ExplainedAuditIssue":
        if not self.unsupported_clause.strip() or not self.explanation.strip():
            raise ValueError("narrative rejection requires a nonempty clause and explanation")
        return self


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


class AuditDecisionError(ValueError):
    def __init__(self, message: str, *, missing_paths: tuple[str, ...] = (), unknown_count: int = 0,
                 duplicate_count: int = 0) -> None:
        super().__init__(message)
        self.diagnostics = {"missing_paths": list(missing_paths), "unknown_path_count": unknown_count,
                            "duplicate_path_count": duplicate_count}


class FindingAuditResult(StrictModel):
    """An exhaustive finding check, with concise display-intended rejection reasons."""

    finding_checks: tuple[FindingDecision, ...] = Field(max_length=40)
    other_issues: tuple[ExplainedAuditIssue, ...] = Field(default=(), max_length=60)

    def as_audit(self, supplied: DecisionAuditorInput) -> tuple[AuditResult, dict[str, Any]]:
        expected = {finding.field_path: finding
                    for finding in (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons)}
        paths = [check.field_path for check in self.finding_checks]
        if len(set(paths)) != len(paths) or set(paths) != set(expected):
            raise AuditDecisionError("audit decision coverage must match every original finding exactly",
                missing_paths=tuple(sorted(set(expected) - set(paths))),
                unknown_count=len(set(paths) - set(expected)), duplicate_count=len(paths) - len(set(paths)))
        issues = []
        rejections = []
        for check in self.finding_checks:
            finding = expected[check.field_path]
            quotes = {quote.evidence_ref: quote for quote in finding.citations}
            if len(set(check.evidence_refs)) != len(check.evidence_refs) or not set(check.evidence_refs) <= quotes.keys():
                raise ValueError("audit decision cites unknown, foreign, or duplicate evidence")
            if check.supported:
                owners = {quotes[ref].source_ref for ref in check.evidence_refs if quotes[ref].support_type == "supports"}
                if not set(finding.source_refs) <= owners:
                    raise ValueError("supported decision must cite supporting evidence for each owner")
                continue
            if _normalized(check.unsupported_clause or "") not in _normalized(finding.statement):
                raise ValueError("audit rejection must identify an exact clause in its finding")
            code = {"quantity": "numeric_mismatch", "scope": "scope_mismatch", "polarity": "negation_mismatch"}.get(
                check.category or "", "unsupported_finding")
            issues.append(AuditIssue(code=code, field_path=check.field_path, retryable=True))
            rejections.append(check.model_dump(mode="json"))

        draft = supplied.report_draft
        narrative = {"report_draft.summary": draft.summary}
        for field in ("who_should_buy", "who_should_avoid"):
            for index, text in enumerate(getattr(draft, field)):
                narrative[f"report_draft.{field}[{index}]"] = text
        allowed_quotes = {quote.evidence_ref for finding in expected.values() for quote in finding.citations}
        for index, disagreement in enumerate(draft.disagreements):
            prefix = f"report_draft.disagreements[{index}]"
            narrative[prefix] = " ".join((disagreement.topic, disagreement.side_a, disagreement.side_b))
            narrative[prefix + ".side_a"] = disagreement.side_a
            narrative[prefix + ".side_b"] = disagreement.side_b
            allowed_quotes.update(quote.evidence_ref for quote in (*disagreement.side_a_citations, *disagreement.side_b_citations))
        seen = set()
        for issue in self.other_issues:
            text = narrative.get(issue.field_path)
            is_disagreement = issue.field_path.startswith("report_draft.disagreements[")
            if text is None or (issue.code == "unsupported_disagreement") != is_disagreement:
                raise ValueError("unknown or mismatched narrative audit path")
            if issue.field_path in seen or _normalized(issue.unsupported_clause) not in _normalized(text):
                raise ValueError("duplicate narrative rejection or invalid rejection span")
            scoped_quotes = allowed_quotes
            if is_disagreement:
                scoped_quotes = set()
                for index, disagreement in enumerate(draft.disagreements):
                    prefix = f"report_draft.disagreements[{index}]"
                    if issue.field_path == prefix:
                        scoped_quotes.update(q.evidence_ref for q in (*disagreement.side_a_citations, *disagreement.side_b_citations))
                    elif issue.field_path == prefix + ".side_a":
                        scoped_quotes.update(q.evidence_ref for q in disagreement.side_a_citations)
                    elif issue.field_path == prefix + ".side_b":
                        scoped_quotes.update(q.evidence_ref for q in disagreement.side_b_citations)
            if len(set(issue.evidence_refs)) != len(issue.evidence_refs) or not set(issue.evidence_refs) <= scoped_quotes:
                raise ValueError("narrative audit cites unknown evidence")
            seen.add(issue.field_path)
            issues.append(AuditIssue(code=issue.code, field_path=issue.field_path, retryable=True))
            rejections.append(issue.model_dump(mode="json"))
        return (AuditResult(verdict="fail" if issues else "pass", issues=tuple(issues)),
                {"finding_checks": [check.model_dump(mode="json") for check in self.finding_checks],
                 "rejections": rejections})


def finding_audit_schema(supplied: DecisionAuditorInput) -> dict[str, Any]:
    """Constrain generation to this report's paths and valid decision shapes.

    The persisted compiled contract remains unchanged. Coverage, ownership and
    clause checks still run in code; schema constraints do not approve evidence.
    """
    schema = FindingAuditResult.model_json_schema()
    paths = [finding.field_path for finding in
             (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons)]
    schema["properties"]["finding_checks"].update(minItems=len(paths), maxItems=len(paths))
    decision = schema["$defs"]["FindingDecision"]
    common = copy.deepcopy(decision)
    if paths:
        common["properties"]["field_path"]["enum"] = paths
    branches = []
    for supported in (True, False):
        branch = copy.deepcopy(common)
        branch["properties"]["supported"] = {"type": "boolean", "const": supported}
        if supported:
            for field in ("category", "unsupported_clause", "explanation"):
                branch["properties"][field] = {"type": "null"}
        else:
            for field in ("category", "unsupported_clause", "explanation"):
                prop = branch["properties"][field]
                nonnull = next(option for option in prop.pop("anyOf") if option.get("type") != "null")
                prop.update(nonnull)
                if field != "category":
                    prop["minLength"] = 1
        branches.append(branch)
    schema["$defs"]["FindingDecision"] = {"anyOf": branches}
    # Narrative decisions have their own exact paths and category. Limitations
    # are context, not findings to reject at a fabricated indexed report path.
    narrative_paths = ["report_draft.summary"]
    for field in ("who_should_buy", "who_should_avoid"):
        narrative_paths.extend(f"report_draft.{field}[{index}]" for index, _ in enumerate(getattr(supplied.report_draft, field)))
    disagreement_paths = [f"report_draft.disagreements[{index}]{suffix}"
        for index, _ in enumerate(supplied.report_draft.disagreements) for suffix in ("", ".side_a", ".side_b")]
    issue_branches = []
    for code, allowed_paths in (("unsupported_narrative", narrative_paths), ("unsupported_disagreement", disagreement_paths)):
        if allowed_paths:
            branch = copy.deepcopy(schema["$defs"]["ExplainedAuditIssue"])
            branch["properties"]["code"] = {"type": "string", "const": code}
            branch["properties"]["field_path"]["enum"] = allowed_paths
            issue_branches.append(branch)
    schema["$defs"]["ExplainedAuditIssue"] = {"anyOf": issue_branches}
    return schema
