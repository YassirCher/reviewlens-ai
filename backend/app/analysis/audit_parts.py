"""Server-owned rejection spans: models select a part, never retype its wording."""
from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from app.analysis.audit import (
    AuditCitation, AuditDecisionError, DecisionAuditorInput, FindingAuditResult,
    decision_audit_input,
)
from app.analysis.contracts import AuditResult, StrictModel
from app.analysis.synthesis import CatalogSource


class AuditPart(StrictModel):
    part_ref: str = Field(pattern=r"^p[1-9]\d*$")
    text: str = Field(min_length=1, max_length=200)


class PartFinding(StrictModel):
    field_path: str
    statement: tuple[AuditPart, ...] = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    citations: tuple[AuditCitation, ...] = Field(min_length=1, max_length=30)


class PartDisagreement(StrictModel):
    topic: tuple[AuditPart, ...] = Field(min_length=1)
    side_a: tuple[AuditPart, ...] = Field(min_length=1)
    side_a_source_refs: tuple[str, ...]
    side_a_citations: tuple[AuditCitation, ...]
    side_b: tuple[AuditPart, ...] = Field(min_length=1)
    side_b_source_refs: tuple[str, ...]
    side_b_citations: tuple[AuditCitation, ...]


class PartAuditDraft(StrictModel):
    product_display_name: str
    product_canonical_name: str
    summary: tuple[AuditPart, ...] = Field(min_length=1)
    consensus_pros: tuple[PartFinding, ...]
    consensus_cons: tuple[PartFinding, ...]
    disagreements: tuple[PartDisagreement, ...]
    longest_usage_period: str | None
    longest_usage_source_ref: str | None
    who_should_buy: tuple[tuple[AuditPart, ...], ...]
    who_should_avoid: tuple[tuple[AuditPart, ...], ...]
    limitations: tuple[str, ...]


class PartAuditorInput(StrictModel):
    report_draft: PartAuditDraft
    sources: tuple[CatalogSource, ...] = Field(min_length=1, max_length=8)

    def binding(self) -> tuple[DecisionAuditorInput, dict[str, tuple[str, set[str]]]]:
        payload = self.model_dump(mode="json")
        draft = payload["report_draft"]
        bindings: dict[str, tuple[str, set[str]]] = {}

        def join(parts: list[dict], *paths: str) -> str:
            for part in parts:
                if part["part_ref"] in bindings:
                    raise AuditDecisionError("duplicate supplied audit part")
                bindings[part["part_ref"]] = (part["text"], set(paths))
            return "".join(part["text"] for part in parts)

        draft["summary"] = join(draft["summary"], "report_draft.summary")
        for field in ("consensus_pros", "consensus_cons"):
            for finding in draft[field]:
                finding["statement"] = join(finding["statement"], finding["field_path"])
        for field in ("who_should_buy", "who_should_avoid"):
            draft[field] = [join(parts, f"report_draft.{field}[{index}]")
                            for index, parts in enumerate(draft[field])]
        for index, disagreement in enumerate(draft["disagreements"]):
            prefix = f"report_draft.disagreements[{index}]"
            disagreement["topic"] = join(disagreement["topic"], prefix)
            for side in ("side_a", "side_b"):
                disagreement[side] = join(disagreement[side], prefix, prefix + "." + side)
        return DecisionAuditorInput.model_validate(payload), bindings


def part_audit_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Partition text once, losslessly, within the existing exact-clause bound."""
    supplied = decision_audit_input(payload)
    counter = 0

    def parts(text: str) -> list[dict]:
        nonlocal counter
        result = []
        while text:
            end = min(len(text), 200)
            if end < len(text):
                # Preserve whitespace in the selected span so joining restores exact bytes.
                boundaries = [match.end() for match in re.finditer(r"\s+", text[:end])]
                end = boundaries[-1] if boundaries else end
            counter += 1
            result.append({"part_ref": f"p{counter}", "text": text[:end]})
            text = text[end:]
        return result

    draft = supplied["report_draft"]
    draft["summary"] = parts(draft["summary"])
    for field in ("consensus_pros", "consensus_cons"):
        for finding in draft[field]:
            finding["statement"] = parts(finding["statement"])
    for field in ("who_should_buy", "who_should_avoid"):
        draft[field] = [parts(text) for text in draft[field]]
    for disagreement in draft["disagreements"]:
        for field in ("topic", "side_a", "side_b"):
            disagreement[field] = parts(disagreement[field])
    return supplied


Category = Literal["material", "quantity", "condition", "attribution", "scope", "polarity", "measurement"]


class ReferencedFindingDecision(StrictModel):
    field_path: str = Field(min_length=1, max_length=300)
    supported: bool
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=30)
    category: Category | None
    rejected_part_ref: str | None = Field(pattern=r"^p[1-9]\d*$")
    explanation: str | None = Field(max_length=180)

    @model_validator(mode="after")
    def validate_rejection(self) -> "ReferencedFindingDecision":
        if self.supported:
            if any(value is not None for value in (self.category, self.rejected_part_ref, self.explanation)):
                raise ValueError("supported finding cannot contain rejection fields")
        elif not self.category or not self.rejected_part_ref or not (self.explanation or "").strip():
            raise ValueError("rejected finding requires category, owned part reference and explanation")
        return self


class ReferencedNarrativeIssue(StrictModel):
    code: Literal["unsupported_narrative", "unsupported_disagreement"]
    field_path: str = Field(min_length=1, max_length=300)
    evidence_refs: tuple[str, ...] = Field(max_length=30)
    rejected_part_ref: str = Field(pattern=r"^p[1-9]\d*$")
    explanation: str = Field(min_length=1, max_length=180)


class ReferencedAuditResult(StrictModel):
    finding_checks: tuple[ReferencedFindingDecision, ...] = Field(max_length=40)
    other_issues: tuple[ReferencedNarrativeIssue, ...] = Field(default=(), max_length=60)

    def as_audit(self, supplied: PartAuditorInput) -> tuple[AuditResult, dict[str, Any]]:
        original, bindings = supplied.binding()
        payload = self.model_dump(mode="json")
        selected = []
        for field in ("finding_checks", "other_issues"):
            for index, decision in enumerate(payload[field]):
                ref = decision.pop("rejected_part_ref")
                clause = None
                if ref is not None:
                    bound = bindings.get(ref)
                    if bound is None or decision["field_path"] not in bound[1]:
                        raise AuditDecisionError("unknown or foreign rejection part", issues=({
                            "loc": [field, index, "rejected_part_ref"], "field_path": decision["field_path"],
                            "part_ref": ref, "type": "unknown_rejection_part" if bound is None else "foreign_rejection_part"},))
                    clause = bound[0]
                    selected.append({"field_path": decision["field_path"], "part_ref": ref})
                decision["unsupported_clause"] = clause
        # All existing coverage, citation ownership, polarity categories and exact span checks remain mandatory.
        audit, diagnostics = FindingAuditResult.model_validate(payload).as_audit(original)
        diagnostics["selected_parts"] = selected
        return audit, diagnostics


def referenced_audit_schema(supplied: PartAuditorInput) -> dict[str, Any]:
    schema = ReferencedAuditResult.model_json_schema()
    original, parts = supplied.binding()
    paths = [finding.field_path for finding in (*original.report_draft.consensus_pros, *original.report_draft.consensus_cons)]
    schema["properties"]["finding_checks"].update(minItems=len(paths), maxItems=len(paths))
    branches = []
    for supported in (True, False):
        branch = copy.deepcopy(schema["$defs"]["ReferencedFindingDecision"])
        if paths:
            branch["properties"]["field_path"]["enum"] = paths
        branch["properties"]["supported"] = {"type": "boolean", "const": supported}
        for field in ("category", "rejected_part_ref", "explanation"):
            if supported:
                branch["properties"][field] = {"type": "null"}
            else:
                prop = branch["properties"][field]
                prop.update(next(option for option in prop.pop("anyOf") if option.get("type") != "null"))
                if field == "rejected_part_ref":
                    prop["enum"] = list(parts)
                elif field == "explanation":
                    prop["minLength"] = 1
        branches.append(branch)
    schema["$defs"]["ReferencedFindingDecision"] = {"anyOf": branches}
    narrative = ["report_draft.summary"]
    for field in ("who_should_buy", "who_should_avoid"):
        narrative.extend(f"report_draft.{field}[{index}]" for index, _ in enumerate(getattr(original.report_draft, field)))
    disagreements = [f"report_draft.disagreements[{index}]{suffix}" for index, _ in enumerate(original.report_draft.disagreements)
                     for suffix in ("", ".side_a", ".side_b")]
    branches = []
    for code, allowed in (("unsupported_narrative", narrative), ("unsupported_disagreement", disagreements)):
        if allowed:
            branch = copy.deepcopy(schema["$defs"]["ReferencedNarrativeIssue"])
            branch["properties"]["code"] = {"type": "string", "const": code}
            branch["properties"]["field_path"]["enum"] = allowed
            branch["properties"]["rejected_part_ref"]["enum"] = list(parts)
            branches.append(branch)
    schema["$defs"]["ReferencedNarrativeIssue"] = {"anyOf": branches}
    return schema


class OwnedFindingDecision(StrictModel):
    supported: bool
    category: Category | None
    rejected_part_ref: str | None = Field(pattern=r"^p[1-9]\d*$")
    explanation: str | None = Field(max_length=180)

    @model_validator(mode="after")
    def rejection_fields(self) -> "OwnedFindingDecision":
        if self.supported:
            if any(value is not None for value in (self.category, self.rejected_part_ref, self.explanation)):
                raise ValueError("supported decision contains rejection fields")
        elif not self.category or not self.rejected_part_ref or not (self.explanation or "").strip():
            raise ValueError("rejection needs a category, owned part and defect explanation")
        return self


class OwnedNarrativeIssue(StrictModel):
    code: Literal["unsupported_narrative", "unsupported_disagreement"]
    field_path: str = Field(min_length=1, max_length=300)
    rejected_part_ref: str = Field(pattern=r"^p[1-9]\d*$")
    explanation: str = Field(min_length=1, max_length=180)


class OwnedAuditResult(StrictModel):
    decisions: dict[str, OwnedFindingDecision]
    other_issues: tuple[OwnedNarrativeIssue, ...] = Field(default=(), max_length=60)

    def as_audit(self, supplied: PartAuditorInput) -> tuple[AuditResult, dict[str, Any]]:
        original, _ = supplied.binding()
        findings = {f.field_path: f for f in (*original.report_draft.consensus_pros, *original.report_draft.consensus_cons)}
        if set(self.decisions) != set(findings):
            raise AuditDecisionError("audit paths must cover every original finding exactly", issues=({
                "loc": ["decisions"], "type": "decision_coverage", "missing_paths": sorted(set(findings) - set(self.decisions)),
                "unknown_paths": sorted(set(self.decisions) - set(findings))},))
        checks = [{**decision.model_dump(mode="json"), "field_path": path,
                   "evidence_refs": [citation.evidence_ref for citation in findings[path].citations]}
                  for path, decision in self.decisions.items()]
        # Narrative issues contain no model-selected citations either. The exact part/path
        # checks still run through the compiled adapter.
        issues = [{**issue.model_dump(mode="json"), "evidence_refs": []} for issue in self.other_issues]
        audit, diagnostics = ReferencedAuditResult.model_validate({"finding_checks": checks, "other_issues": issues}).as_audit(supplied)
        diagnostics["citation_binding"] = "server_owned"
        return audit, diagnostics


def owned_audit_schema(supplied: PartAuditorInput) -> dict[str, Any]:
    schema = OwnedAuditResult.model_json_schema()
    _, parts = supplied.binding()
    properties = {}
    for finding in (*supplied.report_draft.consensus_pros, *supplied.report_draft.consensus_cons):
        branches = []
        for supported in (True, False):
            branch = copy.deepcopy(schema["$defs"]["OwnedFindingDecision"])
            branch["properties"]["supported"] = {"type": "boolean", "const": supported}
            for field in ("category", "rejected_part_ref", "explanation"):
                prop = branch["properties"][field]
                if supported:
                    branch["properties"][field] = {"type": "null"}
                else:
                    prop.update(next(option for option in prop.pop("anyOf") if option.get("type") != "null"))
                    if field == "rejected_part_ref":
                        prop["enum"] = [ref for ref, (_, paths) in parts.items() if finding.field_path in paths]
                    elif field == "explanation":
                        prop["minLength"] = 1
            branches.append(branch)
        properties[finding.field_path] = {"anyOf": branches}
    schema["properties"]["decisions"] = {"type": "object", "properties": properties,
                                          "required": list(properties), "additionalProperties": False}
    narrative_paths = {path for _, paths in parts.values() for path in paths
                       if not path.startswith(("report_draft.consensus_pros[", "report_draft.consensus_cons["))}
    branches = []
    for path in sorted(narrative_paths):
        branch = copy.deepcopy(schema["$defs"]["OwnedNarrativeIssue"])
        branch["properties"]["field_path"] = {"type": "string", "const": path}
        branch["properties"]["code"] = {"type": "string", "const":
            "unsupported_disagreement" if path.startswith("report_draft.disagreements[") else "unsupported_narrative"}
        branch["properties"]["rejected_part_ref"]["enum"] = [ref for ref, (_, paths) in parts.items() if path in paths]
        branches.append(branch)
    schema["$defs"]["OwnedNarrativeIssue"] = {"anyOf": branches}
    return schema
