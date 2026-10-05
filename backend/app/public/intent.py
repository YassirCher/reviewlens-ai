"""Deterministic product scope; no catalog guesses, inference, or outbound calls."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RESOLVER_VERSION = "product-intent-v1"


class IntentConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_name: str = Field(min_length=1, max_length=500)
    exact_model: Literal[True]


class IntentResolution(BaseModel):
    status: Literal["resolved", "requires_clarification"]
    reason: str | None = None
    question: str | None = None
    canonical_name: str | None = None
    resolver_version: str = RESOLVER_VERSION


def normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def identity_text(value: str) -> str:
    text = normalized(value).replace("–", "-").replace("—", "-")
    text = re.sub(r"(?<=[a-z0-9])[-_](?=[a-z0-9])", " ", text)
    return re.sub(r"\s+(?:headphones?|earbuds?|phones?)$", "", text)


def resolve_intent(name: str, confirmation: IntentConfirmation | None = None) -> IntentResolution:
    display = " ".join(name.split())
    text = normalized(display)
    family_text = identity_text(display)
    # Capacity/color must not supply a missing generation for a known family.
    family_text = re.sub(r"\s+\d+\s*(?:gb|tb|gigabytes?|terabytes?)\b", "", family_text).strip()
    broad = bool(re.fullmatch(
        r"(?:apple\s+)?iphone(?:\s+(?:pro|max|plus|mini|se))?|"
        r"(?:samsung\s+)?galaxy(?:\s+(?:s|a|z|fold|flip)(?:\s+(?:ultra|plus|fe))?)?|"
        r"(?:sony[\s-]*)?xm\d+|sony|apple|samsung|headphones?|earbuds?|phone|laptop|"
        r"(?:apple\s+)?ipad(?:\s+(?:pro|air|mini))?|(?:apple\s+)?macbook(?:\s+(?:pro|air))?",
        family_text))
    # A model identifier must be more than an isolated quantity or generation.
    identified = bool(re.search(r"[a-z][a-z\s-]*\d[a-z\d-]*", family_text))
    if re.match(r"(?:apple\s+)?iphone\b", family_text):
        identified = bool(re.match(r"(?:apple\s+)?iphone\s*\d{1,2}\b", family_text))
        broad = broad or not identified
    known_named = bool(re.fullmatch(
        r"(?:valve\s+)?steam\s*deck(?:\s+oled)?|(?:sony\s+)?(?:ult\s+wear)|"
        r"nothing\s+ear(?:\s*\([ab]\))?|(?:apple\s+)?airpods\s+max", text))
    confirmed = confirmation is not None and confirmation.exact_model and normalized(confirmation.product_name) == text
    if not broad and (identified or known_named or (confirmed and len(text.split()) >= 2)):
        return IntentResolution(status="resolved", canonical_name=display)
    return IntentResolution(status="requires_clarification",
        reason="incomplete_family" if broad else "unknown_model",
        question="Which exact product model do you want to analyze? Include the brand and generation or model name.")


def discovered_choices(product: str, candidates: list[dict]) -> list[dict]:
    """Only suggest literal model identities in supplied review titles."""
    text = identity_text(product)
    if "iphone" in text:
        pattern = re.compile(r"\biPhone\s*\d{1,2}(?:\s*(?:Pro\s*Max|Pro|Plus|Mini))?\b", re.I)
    elif re.search(r"\b(?:wh|wf)[\s-]*1000xm\d+\b", text):
        pattern = re.compile(r"\b(?:WH|WF)[\s-]*1000XM\d+\b", re.I)
    elif "galaxy" in text:
        pattern = re.compile(r"\bGalaxy\s+(?:[SA]\s*\d{1,3}(?:\s*(?:Ultra|Plus|FE))?|(?:Z\s*)?(?:Fold|Flip)\s*\d{1,2})\b", re.I)
    else:
        return []
    groups: dict[str, dict] = {}
    exact_found = False
    target_match = pattern.search(text)
    target = re.sub(r"[^a-z0-9]", "", target_match[0] if target_match else text)
    for candidate in candidates:
        title = candidate.get("title", "")
        for match in pattern.finditer(title):
            label = " ".join(match[0].split())
            key = re.sub(r"[^a-z0-9]", "", label.casefold())
            if key == target:
                exact_found = True
            row = groups.setdefault(key, {"id": key, "product_name": label, "sources": []})
            video_id = candidate.get("video_id", "")
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and len(row["sources"]) < 2:
                row["sources"].append({"title": title[:500], "url": f"https://www.youtube.com/watch?v={video_id}"})
    # Comparisons and sibling noise cannot override an established exact model.
    if exact_found:
        return []
    return [row for _, row in sorted(groups.items()) if row["sources"]][:5]
