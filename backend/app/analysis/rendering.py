"""Successor presentation adapter; code owns identities and drawback capacity."""
from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import Field

from app.analysis.contracts import ConsensusItem, FinalReportDraft, StrictModel
from app.analysis.grounding import finding_narrative
from app.analysis.synthesis import (
    AtomicBuyingSynthesis, CatalogRepairSynthesisInput, EvidenceBoundBuyingSynthesis,
    SynthesisBindingError, catalog_repair_synthesis_input, evidence_catalog,
)

_ALIAS = re.compile(r"\b(?:s\d+|e\d+)\b", re.I)


def topic_key(topic: str) -> str:
    key = " ".join(topic.casefold().replace("_", " ").replace("-", " ").split())
    return {"app": "app support", "app compatibility": "app support", "no app": "app support",
            "application support": "app support", "companion app": "app support", "app availability": "app support"}.get(key, key)


class DrawbackPriority(StrictModel):
    topic: str = Field(min_length=1, max_length=40)
    evidence_ref: str = Field(pattern=r"^e[1-9]\d*$")


class PrioritizedSynthesisInput(CatalogRepairSynthesisInput):
    drawback_priorities: tuple[DrawbackPriority, ...] = Field(default=(), max_length=4)


def prioritized_synthesis_input(payload: dict[str, Any]) -> dict[str, Any]:
    compact = catalog_repair_synthesis_input(payload)
    catalog, bindings, _ = evidence_catalog(payload["source_analyses"])
    confidence = {q["evidence_ref"]: q["confidence"] for q in catalog["evidence_catalog"]}
    metadata = {str(eid): item for item in payload.get("claim_catalog", []) if item["kind"] == "caveat"
                for eid in item["evidence_node_ids"]}
    priorities: dict[str, dict[str, Any]] = {}
    for quote in compact["evidence_catalog"]:
        owner, eid = bindings[quote["evidence_ref"]]
        item = metadata.get(eid)
        if item and str(item["source_id"]) == owner and quote["support_type"] == "supports":
            topic = topic_key(item["topic"])
            current = priorities.get(topic)
            if current is None or confidence[quote["evidence_ref"]] > current["confidence"]:
                priorities[topic] = {"topic": item["topic"], "evidence_ref": quote["evidence_ref"],
                                     "confidence": confidence[quote["evidence_ref"]], "central": item.get("central", False)}
    compact["drawback_priorities"] = [{"topic": row["topic"], "evidence_ref": row["evidence_ref"]}
        for row in sorted(priorities.values(), key=lambda row: (not row["central"], -row["confidence"], row["topic"]))[:4]]
    return compact


def _render_prose(text: str, *, owners: set[str], refs: set[str], sources: dict,
                  product: str, excerpts: str, require_observation: bool = False) -> tuple[str | None, str | None]:
    """Only known owned aliases change presentation. Ambiguity never changes ownership."""
    def protected(token: str) -> bool:
        pattern = r"\b" + re.escape(token) + r"\b"
        return bool(re.search(pattern, product, re.I) or re.search(pattern, excerpts, re.I))

    # Reference-only annotations are presentation, not test conditions.
    def annotation(match: re.Match) -> str:
        inner = match[0][1:-1]
        tokens = _ALIAS.findall(inner)
        residue = _ALIAS.sub("", inner).strip(" ,;:/")
        if tokens and not residue and all(t.casefold() in refs and not protected(t) for t in tokens):
            return ""
        return match[0]

    text = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", annotation, text)
    error = None

    def alias(match: re.Match) -> str:
        nonlocal error
        token = match[0]
        key = token.casefold()
        if protected(token):
            return token
        if key in sources and key in owners:
            return "the reviewer"
        if key in refs:
            return "the cited excerpt"
        error = "foreign_prose_reference" if key in sources else "unresolved_prose_reference"
        return token

    rendered = " ".join(_ALIAS.sub(alias, text).split()).strip()
    tokens = _ALIAS.findall(text)
    if require_observation and not error and rendered.casefold().strip(" .,:;") in {"the cited excerpt", "the reviewer"}:
        return None, "reference_only_prose"
    if (require_observation and not error and tokens and
            not _ALIAS.sub("", text).strip(" ,;:/()[]") and all(not protected(token) for token in tokens)):
        return None, "reference_only_prose"
    return (None, error) if error else (rendered, None)


class NormalizedBuyingSynthesis(EvidenceBoundBuyingSynthesis):
    """Compiled successor; older evidence/source-bound contracts retain their adapters."""

    deduplicate_assertions: ClassVar[bool] = False
    owned_narrative: ClassVar[bool] = False

    def as_report(self, display_name: str, canonical_name: str, reviews: list[dict]) -> FinalReportDraft:
        return self.compile_report(display_name, canonical_name, reviews)[0]

    def compile_report(self, display_name: str, canonical_name: str, reviews: list[dict],
                       claim_catalog: list[dict] | None = None) -> tuple[FinalReportDraft, list[dict]]:
        catalog, bindings, sources = evidence_catalog(reviews)
        quotes = {q["evidence_ref"]: q for q in catalog["evidence_catalog"]}
        diagnostics: list[dict] = []
        assertions = []
        identities: set[tuple] = set()
        for index, assertion in enumerate(self.assertions):
            refs = tuple(dict.fromkeys(assertion.evidence_refs))
            if any(ref not in bindings for ref in refs):
                raise SynthesisBindingError("unknown_reference", index, reference_field="evidence_refs")
            owners = {bindings[ref][0] for ref in refs}
            if len(owners) != 1:
                raise SynthesisBindingError("assertion_source_mismatch", index, reference_field="evidence_refs")
            owner_refs = {key for key, owner in sources.items() if owner in owners}
            updates: dict[str, Any] = {"evidence_refs": refs}
            if self.owned_narrative:
                attribute = topic_key(assertion.attribute).capitalize()
                observation = assertion.observation
                conditions = assertion.conditions
                if conditions and re.search(r"\b" + re.escape(" ".join(conditions.casefold().split()).strip(" .")) + r"\b",
                                            " ".join(observation.casefold().split())):
                    conditions = None
                assertion = assertion.model_copy(update={"attribute": attribute, "observation": observation, "conditions": conditions})
            rejected = False
            for field in ("attribute", "observation", "conditions"):
                value = getattr(assertion, field)
                if value is None:
                    continue
                rendered, error = _render_prose(value, owners=owner_refs, refs=set(refs), sources=sources,
                    product=canonical_name, excerpts=" ".join(quotes[ref]["excerpt"] for ref in refs),
                    require_observation=field == "observation")
                if error:
                    diagnostics.append({"loc": ["assertions", index, field], "type": error,
                                        "action": "omitted", "reference_category": "prose",
                                        "ownership": "foreign" if error == "foreign_prose_reference" else
                                                     "owned" if error == "reference_only_prose" else "unresolved"})
                    rejected = True
                    break
                updates[field] = rendered or (None if field == "conditions" else "Reviewer observation")
                if rendered != value:
                    diagnostics.append({"loc": ["assertions", index, field], "type": "owned_alias_rendered", "action": "normalized",
                                        "reference_category": "prose", "ownership": "owned"})
            if not rejected:
                if self.owned_narrative and not re.search(r"\b(?:reviewer|review|reported|claimed|measured|tested|observed)\b", updates["observation"], re.I):
                    updates["observation"] = "The reviewer reports: " + updates["observation"]
                normalized_assertion = assertion.model_copy(update=updates)
                identity = (normalized_assertion.kind, next(iter(owners)), tuple(sorted(refs)),
                    *(" ".join((getattr(normalized_assertion, field) or "").casefold().split())
                      for field in ("attribute", "observation", "conditions")))
                if self.deduplicate_assertions and identity in identities:
                    diagnostics.append({"loc": ["assertions", index], "type": "duplicate_assertion",
                                        "action": "deduplicated", "ownership": "owned"})
                    continue
                identities.add(identity)
                assertions.append(normalized_assertion)

        # Optional reference fields remain strict, independently of prose normalization.
        disagreements = []
        for index, disagreement in enumerate(self.disagreements):
            updates = {}
            for side in ("a", "b"):
                field = f"side_{side}_evidence_refs"
                refs = tuple(dict.fromkeys(getattr(disagreement, field)))
                if any(ref not in bindings for ref in refs):
                    raise SynthesisBindingError("unknown_reference", index, "disagreements", field)
                owners = {key for key, owner in sources.items() if owner in {bindings[ref][0] for ref in refs}}
                text, error = _render_prose(getattr(disagreement, f"side_{side}"), owners=owners, refs=set(refs),
                    sources=sources, product=canonical_name, excerpts=" ".join(quotes[ref]["excerpt"] for ref in refs),
                    require_observation=True)
                if error:
                    diagnostics.append({"loc": ["disagreements", index, f"side_{side}"], "type": error, "action": "omitted"})
                    break
                updates[field], updates[f"side_{side}"] = refs, text
            else:
                topic, error = _render_prose(disagreement.topic, owners=set(sources), refs=set(quotes),
                                           sources=sources, product=canonical_name, excerpts="")
                if not error:
                    disagreements.append(disagreement.model_copy(update={**updates, "topic": topic}))
        if self.longest_usage_source_ref is not None and self.longest_usage_source_ref not in sources:
            raise SynthesisBindingError("unknown_reference", None, "longest_usage_source_ref")
        normalized = self.model_copy(update={"assertions": tuple(assertions), "disagreements": tuple(disagreements)})
        draft = AtomicBuyingSynthesis.as_report(normalized, display_name, canonical_name, reviews)
        all_excerpts = " ".join(quote["excerpt"] for quote in quotes.values())
        narrative = {}
        for field in ("summary", "who_should_buy", "who_should_avoid", "limitations", "longest_usage_period"):
            original = getattr(draft, field)
            values = (original,) if isinstance(original, str) else (original or ())
            rendered_values = []
            for index, value in enumerate(values):
                rendered, error = _render_prose(value, owners=set(sources), refs=set(quotes), sources=sources,
                                              product=canonical_name, excerpts=all_excerpts)
                if error:
                    diagnostics.append({"loc": [field, index], "type": error, "action": "omitted"})
                elif rendered:
                    rendered_values.append(rendered)
            narrative[field] = ((rendered_values[0] if rendered_values else "Cited reviewer findings are presented below.")
                                if field == "summary" else
                                (rendered_values[0] if rendered_values else None) if field == "longest_usage_period"
                                else tuple(rendered_values))
        draft = draft.model_copy(update=narrative)
        if not assertions:
            # A zero-finding draft must reach the existing bounded repair gate.
            return draft, diagnostics[:96]
        pros, cons = list(draft.consensus_pros), list(draft.consensus_cons)
        covered = {str(eid) for item in cons for eid in item.evidence_node_ids}
        reverse = {eid: ref for ref, (_, eid) in bindings.items()}
        topics: set[str] = set()
        for item in sorted(claim_catalog or [], key=lambda row: (not row.get("central", False), row["topic"].casefold(), row["source_id"])):
            topic = topic_key(item["topic"])
            if item["kind"] != "caveat" or topic in topics or len(topics) >= 4:
                continue
            owned = [reverse[str(eid)] for eid in item["evidence_node_ids"] if str(eid) in reverse
                     and bindings[reverse[str(eid)]][0] == str(item["source_id"])
                     and quotes[reverse[str(eid)]]["support_type"] == "supports"]
            if not owned:
                continue
            topics.add(topic)
            if any(bindings[ref][1] in covered for ref in owned):
                continue
            quote = max((quotes[ref] for ref in owned), key=lambda q: q["confidence"])
            english = item.get("claim") if self.owned_narrative else None
            candidate = ConsensusItem(statement=(f'{topic.capitalize()}: The reviewer reports {english}' if english else
                f'{item["topic"]}: The reviewer states: “{quote["excerpt"]}”'),
                source_ids=(item["source_id"],), evidence_node_ids=tuple(bindings[ref][1] for ref in owned) if english else (bindings[quote["evidence_ref"]][1],))
            quoted_id = bindings[quote["evidence_ref"]][1]
            misplaced = [i for i, pro in enumerate(pros) if {str(eid) for eid in pro.evidence_node_ids} == {quoted_id}]
            if misplaced:
                # Reserve the declared drawback by replacing its misplaced strength;
                # the auditor judges the attributed quotation's meaning and kind.
                pros.pop(misplaced[-1])
            elif len(pros) + len(cons) >= 12:
                if not pros:
                    continue
                # Preserve better-supported strengths when capacity is full.
                remove = min(range(len(pros)), key=lambda i: (max((quotes[reverse[str(eid)]]["confidence"]
                    for eid in pros[i].evidence_node_ids), default=0), -i))
                pros.pop(remove)
            cons.append(candidate)
            covered.add(quoted_id)
            diagnostics.append({"type": "drawback_quote_reserved", "action": "added", "evidence_ref": quote["evidence_ref"]})
        if pros != list(draft.consensus_pros) or cons != list(draft.consensus_cons):
            draft = draft.model_copy(update={"consensus_pros": tuple(pros), "consensus_cons": tuple(cons),
                                            "who_should_buy": (), "who_should_avoid": ()})
        if self.owned_narrative:
            draft = finding_narrative(draft, len(reviews))
        return draft, diagnostics[:96]


class DistinctBuyingSynthesis(NormalizedBuyingSynthesis):
    """Successor removes exact repeated findings without merging source ownership."""

    deduplicate_assertions: ClassVar[bool] = True


class CompleteBuyingSynthesis(DistinctBuyingSynthesis):
    """English claim fallbacks and narrative generated from owned assertions."""

    owned_narrative: ClassVar[bool] = True
