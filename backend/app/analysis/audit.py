"""Compact citation audit input with the synthesis catalog's server-bound references."""
from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import Field

from app.analysis.contracts import StrictModel
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
