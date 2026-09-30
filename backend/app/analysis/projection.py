"""Deterministic graph findings from already validated source analyses."""
from __future__ import annotations

from typing import Any

from app.analysis.contracts import GraphMutationPlan


def project_claims(reviews: list[dict[str, Any]]) -> GraphMutationPlan:
    groups: dict[str, dict[str, Any]] = {}
    channels: dict[str, str] = {}
    for review in reviews:
        sid = str(review["source_id"])
        channels[sid] = str(review["channel_id"])
        for claim in review["claims"]:
            refs = claim["evidence"]
            if not refs or not any(ref["support_type"] == "supports" for ref in refs):
                continue
            if any(str(ref["source_node_id"]) != sid for ref in refs):
                raise ValueError("projection evidence source mismatch")
            key = " ".join(claim["claim"].casefold().split())
            group = groups.setdefault(key, {"statement": claim["claim"], "source_ids": [],
                                           "evidence_node_ids": [], "confidence": 100, "relation": "scope"})
            group["source_ids"] = list(dict.fromkeys([*group["source_ids"], sid]))
            group["evidence_node_ids"] = list(dict.fromkeys([
                *group["evidence_node_ids"], *(str(ref["evidence_node_id"]) for ref in refs)]))
            group["confidence"] = min(group["confidence"], *(ref["confidence"] for ref in refs))
            if any(ref["support_type"] == "contradicts" for ref in refs):
                group["relation"] = "disagreement"
            elif group["relation"] != "disagreement" and len({channels[s] for s in group["source_ids"]}) >= 2:
                group["relation"] = "consensus"
    return GraphMutationPlan.model_validate({"findings": list(groups.values())})
