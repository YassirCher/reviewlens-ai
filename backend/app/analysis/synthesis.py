"""Nonempty buying synthesis, adapted to the stable persisted report contract."""
from __future__ import annotations

import re
import copy
import uuid
from typing import Any
from typing import Literal

from pydantic import Field, model_validator

from app.analysis.contracts import (
    AuditIssue, AudienceAnalysis, ConsensusAnalystInput, ConsensusItem, Disagreement, FinalReportDraft, StrictModel,
)


class SynthesisInput(ConsensusAnalystInput):
    report_under_repair: FinalReportDraft | None = None


class BuyingFinding(ConsensusItem):
    kind: Literal["strength", "caveat"]
    statement: str = Field(min_length=1, max_length=300)


class BuyingSynthesis(StrictModel):
    summary: str = Field(min_length=1, max_length=1000)
    findings: tuple[BuyingFinding, ...] = Field(min_length=1, max_length=12)
    disagreements: tuple[Disagreement, ...] = Field(default=(), max_length=8)
    longest_usage_period: str | None = Field(default=None, max_length=200)
    longest_usage_source_id: uuid.UUID | None = None
    who_should_buy: tuple[str, ...] = Field(default=(), max_length=6)
    who_should_avoid: tuple[str, ...] = Field(default=(), max_length=6)
    limitations: tuple[str, ...] = Field(default=(), max_length=8)

    def as_report(self, display_name: str, canonical_name: str) -> FinalReportDraft:
        payload = self.model_dump(mode="json", exclude={"findings"})
        payload.update(product_display_name=display_name, product_canonical_name=canonical_name)
        for kind, field in (("strength", "consensus_pros"), ("caveat", "consensus_cons")):
            payload[field] = [item.model_dump(mode="json", exclude={"kind"})
                              for item in self.findings if item.kind == kind]
        return FinalReportDraft.model_validate(payload)


class CatalogSource(StrictModel):
    source_ref: str
    channel_id: str
    review_type: str
    ownership_context: str
    usage_period_raw: str | None
    limitations: tuple[str, ...]


class CatalogEvidence(StrictModel):
    evidence_ref: str
    source_ref: str
    claim: str
    excerpt: str
    support_type: Literal["supports", "contradicts"]
    confidence: int
    timestamp_start_seconds: float | None
    timestamp_end_seconds: float | None


class RepairFinding(StrictModel):
    kind: Literal["strength", "caveat"]
    statement: str
    evidence_refs: tuple[str, ...]


class CatalogRepair(StrictModel):
    summary: str
    findings: tuple[RepairFinding, ...]
    who_should_buy: tuple[str, ...]
    who_should_avoid: tuple[str, ...]
    limitations: tuple[str, ...]


class AtomicSynthesisInput(StrictModel):
    product_display_name: str
    product_canonical_name: str
    requested_source_count: int = Field(ge=1, le=8)
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)
    evidence_catalog: tuple[CatalogEvidence, ...] = Field(min_length=1, max_length=384)
    audience_analyses: tuple[AudienceAnalysis, ...] = Field(default=(), max_length=8)
    correction_issues: tuple[AuditIssue, ...] = Field(default=(), max_length=100)
    report_under_repair: CatalogRepair | None = None


class QuotedEvidence(StrictModel):
    evidence_ref: str
    source_ref: str
    excerpt: str
    support_type: Literal["supports", "contradicts"]
    confidence: int
    timestamp_start_seconds: float | None
    timestamp_end_seconds: float | None


class QuoteSynthesisInput(StrictModel):
    """Only verified quotations can supply an assertion's material details."""

    product_display_name: str
    product_canonical_name: str
    requested_source_count: int = Field(ge=1, le=8)
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)
    evidence_catalog: tuple[QuotedEvidence, ...] = Field(min_length=1, max_length=384)
    audience_analyses: tuple[AudienceAnalysis, ...] = Field(default=(), max_length=8)
    correction_issues: tuple[AuditIssue, ...] = Field(default=(), max_length=100)
    report_under_repair: CatalogRepair | None = None


class AtomicAssertion(StrictModel):
    kind: Literal["strength", "caveat"]
    attribute: str = Field(min_length=1, max_length=80)
    observation: str = Field(min_length=1, max_length=160)
    conditions: str | None = Field(default=None, max_length=160)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)

    def statement(self) -> str:
        return f"{self.attribute}: {self.observation}" + (f" ({self.conditions})" if self.conditions else "")


class AtomicDisagreement(StrictModel):
    topic: str = Field(min_length=1, max_length=160)
    side_a: str = Field(min_length=1, max_length=300)
    side_a_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    side_b: str = Field(min_length=1, max_length=300)
    side_b_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)


class AtomicBuyingSynthesis(StrictModel):
    summary: str = Field(min_length=1, max_length=1000)
    assertions: tuple[AtomicAssertion, ...] = Field(min_length=1, max_length=12)
    disagreements: tuple[AtomicDisagreement, ...] = Field(default=(), max_length=8)
    longest_usage_period: str | None = Field(default=None, max_length=200)
    longest_usage_source_ref: str | None = None
    who_should_buy: tuple[str, ...] = Field(default=(), max_length=6)
    who_should_avoid: tuple[str, ...] = Field(default=(), max_length=6)
    limitations: tuple[str, ...] = Field(default=(), max_length=8)

    def as_report(self, display_name: str, canonical_name: str,
                  reviews: list[dict[str, Any]]) -> FinalReportDraft:
        _, bindings, sources = evidence_catalog(reviews)

        def bind(refs: tuple[str, ...]) -> tuple[list[str], list[str]]:
            if any(ref not in bindings for ref in refs) or len(set(refs)) != len(refs):
                raise ValueError("unknown or duplicate synthesis evidence reference")
            return (list(dict.fromkeys(bindings[ref][0] for ref in refs)),
                    [bindings[ref][1] for ref in refs])

        payload = self.model_dump(mode="json", exclude={"assertions", "disagreements", "longest_usage_source_ref"})
        payload.update(product_display_name=display_name, product_canonical_name=canonical_name)
        for kind, field in (("strength", "consensus_pros"), ("caveat", "consensus_cons")):
            payload[field] = []
            for item in self.assertions:
                if item.kind == kind:
                    source_ids, evidence_ids = bind(item.evidence_refs)
                    payload[field].append({"statement": item.statement(), "source_ids": source_ids,
                                           "evidence_node_ids": evidence_ids})
        payload["disagreements"] = [
            {"topic": item.topic, "side_a": item.side_a,
             "side_a_source_ids": bind(item.side_a_evidence_refs)[0],
             "side_b": item.side_b, "side_b_source_ids": bind(item.side_b_evidence_refs)[0]}
            for item in self.disagreements
        ]
        if self.longest_usage_source_ref is not None and self.longest_usage_source_ref not in sources:
            raise ValueError("unknown synthesis duration source reference")
        payload["longest_usage_source_id"] = sources.get(self.longest_usage_source_ref or "")
        return FinalReportDraft.model_validate(payload)


class SourceBoundAssertion(AtomicAssertion):
    source_ref: str = Field(pattern=r"^s[1-8]$", description="The one source owning every cited excerpt.")


class SynthesisBindingError(ValueError):
    def __init__(self, code: str, index: int | None, field: str = "assertions", reference_field: str | None = None):
        super().__init__(code)
        path: list[str | int] = [field]
        if index is not None:
            path.append(index)
        if reference_field:
            path.append(reference_field)
        self.issue = {"type": code, "loc": path}
        if code in {"unknown_reference", "assertion_source_mismatch"}:
            self.issue.update(reference_category="citation", ownership="unknown" if code == "unknown_reference" else "mixed")


class SourceBoundBuyingSynthesis(AtomicBuyingSynthesis):
    """Successor contract: one reviewer's observation per assertion."""

    assertions: tuple[SourceBoundAssertion, ...] = Field(min_length=1, max_length=12)

    def as_report(self, display_name: str, canonical_name: str,
                  reviews: list[dict[str, Any]]) -> FinalReportDraft:
        _, bindings, sources = evidence_catalog(reviews)
        for index, assertion in enumerate(self.assertions):
            owner = sources.get(assertion.source_ref)
            if owner is None or any(ref not in bindings for ref in assertion.evidence_refs):
                raise SynthesisBindingError("unknown_reference", index)
            if any(bindings[ref][0] != owner for ref in assertion.evidence_refs):
                raise SynthesisBindingError("assertion_source_mismatch", index)
            # Catalog labels are bindings, never product names or observations.
            if re.search(r"\b[se]\d+\b", assertion.statement(), re.I):
                raise SynthesisBindingError("catalog_label_in_prose", index)
        return super().as_report(display_name, canonical_name, reviews)


class EvidenceBoundBuyingSynthesis(AtomicBuyingSynthesis):
    """Ownership is derived from citations, never selected a second time by a model."""

    def as_report(self, display_name: str, canonical_name: str,
                  reviews: list[dict[str, Any]]) -> FinalReportDraft:
        _, bindings, sources = evidence_catalog(reviews)
        for index, assertion in enumerate(self.assertions):
            if any(ref not in bindings for ref in assertion.evidence_refs):
                raise SynthesisBindingError("unknown_reference", index)
            if len({bindings[ref][0] for ref in assertion.evidence_refs}) != 1:
                raise SynthesisBindingError("assertion_source_mismatch", index)
            if re.search(r"\b(?:s[1-8]|e\d+)\b", assertion.statement(), re.I):
                raise SynthesisBindingError("catalog_label_in_prose", index)
        disagreements = []
        for index, item in enumerate(self.disagreements):
            updates = {}
            for field in ("side_a_evidence_refs", "side_b_evidence_refs"):
                refs = getattr(item, field)
                if any(ref not in bindings for ref in refs):
                    raise SynthesisBindingError("unknown_reference", index, "disagreements", field)
                updates[field] = tuple(dict.fromkeys(refs))
            disagreements.append(item.model_copy(update=updates))
        if self.longest_usage_source_ref is not None and self.longest_usage_source_ref not in sources:
            raise SynthesisBindingError("unknown_reference", None, "longest_usage_source_ref")
        # Repeating an identical authorized reference supplies no new evidence.
        # Canonicalize it without another model call; unknown or foreign refs still fail.
        normalized = self.model_copy(update={
            "assertions": tuple(item.model_copy(update={"evidence_refs": tuple(dict.fromkeys(item.evidence_refs))})
                                for item in self.assertions),
            "disagreements": tuple(disagreements),
        })
        return AtomicBuyingSynthesis.as_report(normalized, display_name, canonical_name, reviews)


class RepairTarget(StrictModel):
    field_path: str
    statement: str
    evidence_refs: tuple[str, ...]
    code: str
    unsupported_clause: str | None
    explanation: str = Field(max_length=180)


class CompactEvidence(StrictModel):
    evidence_ref: str
    source_ref: str
    excerpt: str
    support_type: Literal["supports", "contradicts"]


class RepairSynthesisInput(StrictModel):
    product_display_name: str
    product_canonical_name: str
    requested_source_count: int = Field(ge=1, le=8)
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)
    evidence_catalog: tuple[CompactEvidence, ...] = Field(min_length=1, max_length=384)
    audience_analyses: tuple[AudienceAnalysis, ...] = Field(default=(), max_length=8)
    correction_issues: tuple[AuditIssue, ...] = Field(default=(), max_length=100)
    report_under_repair: CatalogRepair | None = None
    repair_targets: tuple[RepairTarget, ...] = Field(default=(), max_length=100)


class CatalogAudienceSignal(StrictModel):
    """Secondary comment summaries use the same source catalog as review excerpts."""

    source_ref: str = Field(pattern=r"^s[1-8]$")
    comments_sampled: int = Field(ge=0, le=30)
    comments_retained: int = Field(ge=0, le=20)
    sampling_limitations: tuple[str, ...] = Field(min_length=1, max_length=10)
    positive_pct: int = Field(ge=0, le=100)
    neutral_pct: int = Field(ge=0, le=100)
    negative_pct: int = Field(ge=0, le=100)
    recurring_pros: tuple[str, ...] = Field(default=(), max_length=15)
    recurring_cons: tuple[str, ...] = Field(default=(), max_length=15)
    repeated_issues: tuple[str, ...] = Field(default=(), max_length=15)
    audience_agrees_with_reviewer: bool | None = None
    confidence_score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_percentages(self) -> "CatalogAudienceSignal":
        if self.positive_pct + self.neutral_pct + self.negative_pct != 100:
            raise ValueError("audience percentages must sum to 100")
        return self


class CatalogRepairSynthesisInput(StrictModel):
    product_display_name: str
    product_canonical_name: str
    requested_source_count: int = Field(ge=1, le=8)
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)
    evidence_catalog: tuple[CompactEvidence, ...] = Field(min_length=1, max_length=384)
    audience_analyses: tuple[CatalogAudienceSignal, ...] = Field(default=(), max_length=8)
    correction_issues: tuple[AuditIssue, ...] = Field(default=(), max_length=100)
    report_under_repair: CatalogRepair | None = None
    repair_targets: tuple[RepairTarget, ...] = Field(default=(), max_length=100)


def evidence_bound_synthesis_schema(supplied: RepairSynthesisInput | CatalogRepairSynthesisInput) -> dict[str, Any]:
    """Constrain every reference field; assertion citations belong to one source."""
    schema = EvidenceBoundBuyingSynthesis.model_json_schema()
    grouped: dict[str, list[str]] = {}
    for quote in supplied.evidence_catalog:
        grouped.setdefault(quote.source_ref, []).append(quote.evidence_ref)
    citations = schema["$defs"]["AtomicAssertion"]["properties"]["evidence_refs"]
    alternatives = []
    for refs in grouped.values():
        choice = copy.deepcopy(citations)
        choice["items"]["enum"] = refs
        alternatives.append(choice)
    schema["$defs"]["AtomicAssertion"]["properties"]["evidence_refs"] = {"anyOf": alternatives}
    for field in ("side_a_evidence_refs", "side_b_evidence_refs"):
        prop = schema["$defs"]["AtomicDisagreement"]["properties"][field]
        prop["items"]["enum"] = [quote.evidence_ref for quote in supplied.evidence_catalog]
    schema["properties"]["longest_usage_source_ref"] = {"anyOf": [
        {"type": "string", "enum": [source.source_ref for source in supplied.sources]}, {"type": "null"},
    ]}
    return schema


def repair_synthesis_input(payload: dict[str, Any]) -> dict[str, Any]:
    compact = quote_synthesis_input(payload)
    compact["evidence_catalog"] = [{key: quote[key] for key in CompactEvidence.model_fields}
                                   for quote in compact["evidence_catalog"]]
    repair = payload.get("report_under_repair")
    targets = []
    if repair:
        _, bindings, _ = evidence_catalog(payload["source_analyses"])
        reverse = {eid: ref for ref, (_, eid) in bindings.items()}
        details = {item["field_path"].removeprefix("report_draft."): item
                   for item in payload.get("audit_diagnostics", {}).get("rejections", [])}
        for issue in payload.get("correction_issues", []):
            path = issue["field_path"].removeprefix("report_draft.")
            match = re.fullmatch(r"(consensus_pros|consensus_cons)\[(\d+)\]", path)
            if not match or int(match[2]) >= len(repair.get(match[1], [])):
                continue
            finding = repair[match[1]][int(match[2])]
            detail = details.get(path, {})
            targets.append({"field_path": "report_draft." + path, "statement": finding["statement"],
                "evidence_refs": [reverse[str(eid)] for eid in finding["evidence_node_ids"]],
                "code": issue["code"], "unsupported_clause": detail.get("unsupported_clause"),
                "explanation": detail.get("explanation") or "The cited excerpts do not support every quantity, condition, or clause; narrow or remove this finding."})
    return {**compact, "repair_targets": targets}


def catalog_repair_synthesis_input(payload: dict[str, Any]) -> dict[str, Any]:
    compact = repair_synthesis_input(payload)
    _, _, sources = evidence_catalog(payload["source_analyses"])
    source_refs = {source_id: ref for ref, source_id in sources.items()}
    fields = set(CatalogAudienceSignal.model_fields) - {"source_ref"}
    compact["audience_analyses"] = [
        {"source_ref": source_refs[str(item["source_id"])], **{field: item[field] for field in fields}}
        for raw in payload.get("audience_analyses", [])
        if str(raw["source_id"]) in source_refs
        for item in [AudienceAnalysis.model_validate(raw).model_dump(mode="json")]
    ]
    return compact


def unchanged_rejected_findings(original: dict[str, Any], corrected: FinalReportDraft,
                                issues: list[dict[str, Any]]) -> list[str]:
    """Changing duration or moving a rejected finding does not repair that finding."""
    def identity(item: dict[str, Any]) -> tuple:
        return (" ".join(item["statement"].casefold().split()),
                tuple(sorted(str(value) for value in item["source_ids"])),
                tuple(sorted(str(value) for value in item["evidence_node_ids"])))

    retained = {identity(item.model_dump(mode="json"))
                for item in (*corrected.consensus_pros, *corrected.consensus_cons)}
    unchanged = []
    for issue in issues:
        path = issue["field_path"].removeprefix("report_draft.")
        match = re.fullmatch(r"(consensus_pros|consensus_cons)(?:\[(\d+)\])?", path)
        if not match:
            continue
        items = original.get(match[1], [])
        indexes = range(len(items)) if match[2] is None else [int(match[2])]
        for index in indexes:
            if index < len(items) and identity(items[index]) in retained:
                unchanged.append(f"report_draft.{match[1]}[{index}]")
    return list(dict.fromkeys(unchanged))


def evidence_catalog(reviews: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, tuple[str, str]], dict[str, str]]:
    """Deterministic short references; UUIDs stay in the server-side binding map."""
    notes: list[dict[str, Any]] = []
    catalog: list[dict[str, Any]] = []
    bindings: dict[str, tuple[str, str]] = {}
    sources: dict[str, str] = {}
    by_id: dict[str, str] = {}
    for index, review in enumerate(sorted(reviews, key=lambda item: str(item["source_id"])), 1):
        source_ref = f"s{index}"
        sources[source_ref] = str(review["source_id"])
        notes.append({"source_ref": source_ref, "channel_id": review["channel_id"],
                      "review_type": review["review_type"], "ownership_context": review.get("ownership_context", "unknown"),
                      "usage_period_raw": review.get("usage_period_raw") if review.get("usage_period_mentioned") else None,
                      "limitations": review.get("limitations", [])})
        for claim in review["claims"]:
            for quote in claim["evidence"]:
                if str(quote["source_node_id"]) != str(review["source_id"]):
                    raise ValueError("evidence is not owned by the assigned source")
                eid = str(quote["evidence_node_id"])
                if eid in by_id:
                    if bindings[by_id[eid]][0] != str(review["source_id"]):
                        raise ValueError("evidence belongs to multiple sources")
                    continue
                key = f"e{len(catalog) + 1}"
                by_id[eid] = key
                bindings[key] = (str(review["source_id"]), eid)
                catalog.append({"evidence_ref": key, "source_ref": source_ref, "claim": claim["claim"],
                                "excerpt": quote["evidence_text"], "support_type": quote["support_type"],
                                "confidence": quote.get("confidence", 0),
                                "timestamp_start_seconds": quote.get("timestamp_start_seconds"),
                                "timestamp_end_seconds": quote.get("timestamp_end_seconds")})
    return {"sources": notes, "evidence_catalog": catalog}, bindings, sources


def compact_synthesis_input(payload: dict[str, Any]) -> dict[str, Any]:
    catalog, bindings, _ = evidence_catalog(payload["source_analyses"])
    reverse = {eid: key for key, (_, eid) in bindings.items()}
    repair = payload.get("report_under_repair")
    if repair:
        repair = {"summary": repair["summary"], "findings": [
            {"kind": kind, "statement": finding["statement"],
             "evidence_refs": [reverse[str(eid)] for eid in finding["evidence_node_ids"] if str(eid) in reverse]}
            for kind, field in (("strength", "consensus_pros"), ("caveat", "consensus_cons"))
            for finding in repair.get(field, [])],
            **{key: repair.get(key, []) for key in ("who_should_buy", "who_should_avoid", "limitations")}}
    return {**catalog, **{key: payload[key] for key in
            ("product_display_name", "product_canonical_name", "requested_source_count")},
            "audience_analyses": payload.get("audience_analyses", []),
            "correction_issues": [{**issue, "evidence_node_ids": []} for issue in payload.get("correction_issues", [])],
            "report_under_repair": repair}


def quote_synthesis_input(payload: dict[str, Any]) -> dict[str, Any]:
    compact = compact_synthesis_input(payload)
    compact["evidence_catalog"] = [{key: value for key, value in quote.items() if key != "claim"}
                                   for quote in compact["evidence_catalog"]]
    return compact
