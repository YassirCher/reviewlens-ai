"""Exhaustive source/report support decisions within the existing audit call."""
from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import Field

from app.analysis.audit import AuditDecisionError
from app.analysis.audit_parts import OwnedAuditResult, OwnedFindingDecision, PartAuditorInput, PartFinding, owned_audit_schema, part_audit_input
from app.analysis.contracts import AuditResult
from app.analysis.synthesis import evidence_catalog


class SupportAuditorInput(PartAuditorInput):
    source_claims: tuple[PartFinding, ...] = Field(default=(), max_length=48)

    def report_input(self) -> PartAuditorInput:
        return PartAuditorInput.model_validate(self.model_dump(exclude={"source_claims"}, mode="json"))

    def all_findings(self) -> dict[str, PartFinding]:
        findings = {item.field_path: item for item in (*self.source_claims, *self.report_draft.consensus_pros, *self.report_draft.consensus_cons)}
        for index, disagreement in enumerate(self.report_draft.disagreements):
            for side in ('side_a', 'side_b'):
                path = f'report_draft.disagreements[{index}].{side}'
                findings[path] = PartFinding(field_path=path, statement=getattr(disagreement, side),
                    source_refs=getattr(disagreement, side + '_source_refs'), citations=getattr(disagreement, side + '_citations'))
        return findings


class SupportDecision(OwnedFindingDecision):
    supporting_parts: dict[str, tuple[str, ...]] = Field(default_factory=dict,
        description="For supported=true, every statement part maps to its owned supporting evidence refs. For false, empty.")


class SupportedAuditResult(OwnedAuditResult):
    decisions: dict[str, SupportDecision]

    def as_audit(self, supplied: SupportAuditorInput) -> tuple[AuditResult, dict[str, Any]]:
        findings = supplied.all_findings()
        if set(self.decisions) != set(findings):
            raise AuditDecisionError("support decisions must cover all original source claims and findings")
        rejected = []
        for path, decision in self.decisions.items():
            finding = findings[path]
            parts = {part.part_ref for part in finding.statement}
            refs = {citation.evidence_ref: citation for citation in finding.citations}
            if decision.supported:
                if set(decision.supporting_parts) != parts:
                    raise AuditDecisionError("positive support must cover every owned statement part")
                for selected in decision.supporting_parts.values():
                    if not selected or len(selected) != len(set(selected)) or not set(selected) <= refs.keys():
                        raise AuditDecisionError("positive support uses missing, duplicate or foreign citations")
                    owners = {refs[ref].source_ref for ref in selected}
                    if not set(finding.source_refs) <= owners:
                        raise AuditDecisionError("each named source must support every statement part")
            elif decision.supporting_parts or decision.rejected_part_ref not in parts:
                raise AuditDecisionError("rejection must select its own part and no supporting parts")
            if path.startswith("source_analyses[") and not decision.supported:
                rejected.append(path)
        disagreements = [{"code": "unsupported_disagreement", "field_path": path,
            "rejected_part_ref": decision.rejected_part_ref, "explanation": decision.explanation}
            for path, decision in self.decisions.items() if '.disagreements[' in path and not decision.supported]
        report = OwnedAuditResult(decisions={path: OwnedFindingDecision.model_validate(
            decision.model_dump(exclude={"supporting_parts"})) for path, decision in self.decisions.items()
            if path.startswith(("report_draft.consensus_pros", "report_draft.consensus_cons"))},
            other_issues=(*self.other_issues, *disagreements))
        audit, diagnostics = report.as_audit(supplied.report_input())
        diagnostics.update(source_claim_rejections=rejected, support_coverage=len(findings),
                           support_contract="source_and_report_parts_v1")
        return audit, diagnostics


def support_audit_input(payload: dict[str, Any]) -> dict[str, Any]:
    output = part_audit_input(payload)
    report = PartAuditorInput.model_validate(output)
    _, bindings = report.binding()
    counter = max((int(key[1:]) for key in bindings), default=0)
    catalog, references, sources = evidence_catalog(payload["source_analyses"])
    quotes = {item["evidence_ref"]: item for item in catalog["evidence_catalog"]}
    reverse = {evidence_id: ref for ref, (_, evidence_id) in references.items()}
    source_refs = {source_id: ref for ref, source_id in sources.items()}
    claims = []
    for source_index, source in enumerate(payload["source_analyses"]):
        for claim_index, claim in enumerate(source["claims"]):
            statement = claim["claim"]
            parts = []
            # Sentence/semicolon clauses are inspected separately; joining is lossless.
            for clause in re.split(r"(?<=[.;])(?=\s)", statement):
                while clause:
                    end = min(len(clause), 200)
                    if end < len(clause):
                        cuts = [m.end() for m in re.finditer(r"\s+", clause[:end])]
                        end = cuts[-1] if cuts else end
                    counter += 1
                    parts.append({"part_ref": f"p{counter}", "text": clause[:end]})
                    clause = clause[end:]
            selected = [reverse[str(item["evidence_node_id"])] for item in claim["evidence"]
                        if str(item["evidence_node_id"]) in reverse and item["support_type"] == "supports"]
            if not selected:
                raise AuditDecisionError("source claim has no owned supporting quotation")
            claims.append({"field_path": f"source_analyses[{source_index}].claims[{claim_index}]", "statement": parts,
                "source_refs": [source_refs[str(source["source_id"])]],
                "citations": [{key: quotes[ref][key] for key in ("evidence_ref", "source_ref", "excerpt", "support_type")}
                              for ref in dict.fromkeys(selected)]})
    output["source_claims"] = claims
    return output


def supported_audit_schema(supplied: SupportAuditorInput) -> dict[str, Any]:
    schema = SupportedAuditResult.model_json_schema()
    old = owned_audit_schema(supplied.report_input())
    schema["$defs"]["OwnedNarrativeIssue"] = old["$defs"]["OwnedNarrativeIssue"]
    properties = {}
    for path, finding in supplied.all_findings().items():
        branches = []
        for supported in (True, False):
            branch = copy.deepcopy(schema["$defs"]["SupportDecision"])
            branch["properties"]["supported"] = {"type": "boolean", "const": supported}
            for field in ("category", "rejected_part_ref", "explanation"):
                prop = branch["properties"][field]
                if supported:
                    branch["properties"][field] = {"type": "null"}
                else:
                    prop.update(next(option for option in prop.pop("anyOf") if option.get("type") != "null"))
                    if field == "rejected_part_ref":
                        prop["enum"] = [part.part_ref for part in finding.statement]
                    elif field == "explanation":
                        prop["minLength"] = 1
            allowed = [citation.evidence_ref for citation in finding.citations]
            bound = {part.part_ref: {"type": "array", "items": {"type": "string", "enum": allowed}, "minItems": 1,
                      "maxItems": len(allowed)} for part in finding.statement} if supported else {}
            branch["properties"]["supporting_parts"] = {"type": "object", "properties": bound,
                "required": list(bound), "additionalProperties": False}
            branch["required"] = list(branch["properties"])
            branches.append(branch)
        properties[path] = {"anyOf": branches}
    schema["properties"]["decisions"] = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    return schema


def verified_sources(reviews: list[dict], rejections: list[str], catalog: list[dict]) -> tuple[list[dict], dict]:
    """Project audited claims and rebuild prose; persisted upstream outputs stay intact."""
    rejected = set(rejections)
    kinds = {(str(item["source_id"]), item["claim"]): item["kind"] for item in catalog}
    retained = []
    removed_claims = 0
    for index, review in enumerate(reviews):
        claims = [claim for claim_index, claim in enumerate(review["claims"])
                  if f"source_analyses[{index}].claims[{claim_index}]" not in rejected]
        removed_claims += len(review["claims"]) - len(claims)
        if not any(claim.get("central") for claim in claims):
            continue
        strengths = [claim["claim"] for claim in claims if kinds.get((str(review["source_id"]), claim["claim"])) == "strength"]
        caveats = [claim["claim"] for claim in claims if kinds.get((str(review["source_id"]), claim["claim"])) == "caveat"]
        summary = "Cited reviewer observations: "
        for claim in claims:
            if len(summary) + len(claim["claim"]) + 1 > 1000:
                break
            summary += claim["claim"] + " "
        retained.append({**review, "claims": claims,
            "recommendation_summary": summary.rstrip(),
            "pros": strengths, "cons": caveats, "major_issues": caveats, "recommended_for": [], "not_recommended_for": [],
            "evidence_quality_score": round(review["evidence_quality_score"] * len(claims) / len(review["claims"]))})
    return retained, {"source_claims_removed": removed_claims, "sources_removed": len(reviews) - len(retained)}
