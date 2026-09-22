"""Bounded, source-grounded product details extracted from selected YouTube videos."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceDraft(StrictModel):
    source_part: Literal["title", "description", "transcript"]
    excerpt: str = Field(min_length=3, max_length=300)
    timestamp_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class FactDraft(StrictModel):
    group: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=160)
    scope: str | None = Field(default=None, max_length=160)
    evidence: EvidenceDraft


class VariantDraft(StrictModel):
    dimension: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=160)
    scope: str | None = Field(default=None, max_length=160)
    evidence: EvidenceDraft


class SampleDetailDraft(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=160)
    evidence: EvidenceDraft


class SampleUnitDraft(StrictModel):
    role: str = Field(default="Review unit", min_length=1, max_length=80)
    details: tuple[SampleDetailDraft, ...] = Field(default=(), max_length=12)


class ProductExtractionDraft(StrictModel):
    facts: tuple[FactDraft, ...] = Field(default=(), max_length=24)
    variants: tuple[VariantDraft, ...] = Field(default=(), max_length=24)
    sample_units: tuple[SampleUnitDraft, ...] = Field(default=(), max_length=4)


class ProductAnalystInput(StrictModel):
    canonical_product: str = Field(min_length=2, max_length=200)
    source_id: str = Field(min_length=36, max_length=36)
    transcript_node_id: str = Field(min_length=36, max_length=36)


class ProductEvidence(StrictModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    source_url: str = Field(max_length=100)
    source_part: Literal["title", "description", "transcript"]
    excerpt: str
    timestamp_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_url(self) -> "ProductEvidence":
        if (self.source_part == "transcript") != (self.timestamp_seconds is not None):
            raise ValueError("product evidence timestamp must match its source part")
        expected = f"https://www.youtube.com/watch?v={self.video_id}"
        if self.timestamp_seconds is not None:
            expected += f"&t={int(self.timestamp_seconds)}s"
        if self.source_url != expected:
            raise ValueError("product evidence URL must be constructed from its video ID")
        return self


class ProductFact(StrictModel):
    group: str
    label: str
    value: str
    scope: str | None = None
    evidence: tuple[ProductEvidence, ...]
    conflicting: bool = False


class ProductVariant(StrictModel):
    dimension: str
    value: str
    scope: str | None = None
    evidence: tuple[ProductEvidence, ...]


class SampleDetail(StrictModel):
    label: str
    value: str
    evidence: ProductEvidence


class SampleUnit(StrictModel):
    role: str
    details: tuple[SampleDetail, ...]


class SampleUsed(StrictModel):
    units: tuple[SampleUnit, ...] = ()


class ProductInfo(StrictModel):
    facts: tuple[ProductFact, ...] = ()
    variants: tuple[ProductVariant, ...] = ()
    coverage_note: str = "Details stated in selected reviews; available configurations may differ by model and region."


_SEGMENT = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] (.*)$")
_SOURCE_INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|override)\b.{0,80}\b(?:instruction|prompt|system|developer)\b"
    r"|\b(?:reveal|print|expose)\b.{0,80}\b(?:secret|key|prompt)\b",
    re.IGNORECASE,
)


def source_metadata(body: str) -> dict:
    match = re.search(r"```json\s*\n(.*?)\n```", body, re.DOTALL)
    if not match:
        return {}
    try:
        value = json.loads(match.group(1))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _compact(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _supported_value(value: str, excerpt: str) -> bool:
    literal = re.search(r"(?<!\w)" + re.escape(_normalized(value)) + r"(?!\w)", _normalized(excerpt))
    if literal is not None:
        return True
    if not any(char.isdigit() for char in value):
        return False
    # Allow punctuation and spacing changes (5,000 mAh / 5000mAh) without
    # accepting 5000 as evidence for 15000 or 50000.
    return re.search(r"(?<!\d)" + re.escape(_compact(value)) + r"(?!\d)", _compact(excerpt)) is not None


def validate_evidence(
    draft: EvidenceDraft,
    *,
    value: str,
    title: str,
    description: str,
    transcript_body: str,
    video_id: str,
) -> ProductEvidence | None:
    excerpt = _normalized(draft.excerpt)
    if not excerpt or _SOURCE_INSTRUCTION.search(draft.excerpt) or not _supported_value(value, draft.excerpt):
        return None
    if draft.source_part in {"title", "description"}:
        source_text = title if draft.source_part == "title" else description
        if draft.timestamp_seconds is not None or excerpt not in _normalized(source_text):
            return None
        timestamp = None
    else:
        if draft.timestamp_seconds is None:
            return None
        timestamp = draft.timestamp_seconds
        supported = False
        for line in transcript_body.splitlines():
            segment = _SEGMENT.match(line)
            if not segment:
                continue
            start, end = float(segment.group(1)), float(segment.group(2))
            if start - 1 <= timestamp <= max(start, end) + 1 and excerpt in _normalized(segment.group(3)):
                supported = True
                break
        if not supported:
            return None
    return ProductEvidence(
        video_id=video_id,
        source_url=f"https://www.youtube.com/watch?v={video_id}" + (f"&t={int(timestamp)}s" if timestamp is not None else ""),
        source_part=draft.source_part,
        excerpt=draft.excerpt,
        timestamp_seconds=timestamp,
    )


def validate_extraction(
    draft: ProductExtractionDraft,
    *,
    title: str,
    description: str,
    transcript_body: str,
    video_id: str,
) -> tuple[tuple[ProductFact, ...], tuple[ProductVariant, ...], SampleUsed]:
    def evidence(item: EvidenceDraft, value: str) -> ProductEvidence | None:
        return validate_evidence(
            item, value=value, title=title, description=description,
            transcript_body=transcript_body, video_id=video_id,
        )

    def scoped_evidence(item: EvidenceDraft, value: str, scope: str | None) -> ProductEvidence | None:
        if scope and _compact(scope) not in _compact(" ".join((item.excerpt, title, description))):
            return None
        return evidence(item, value)

    facts = tuple(
        ProductFact(group=item.group, label=item.label, value=item.value, scope=item.scope, evidence=(ref,))
        for item in draft.facts if (ref := scoped_evidence(item.evidence, item.value, item.scope)) is not None
    )
    variants = tuple(
        ProductVariant(dimension=item.dimension, value=item.value, scope=item.scope, evidence=(ref,))
        for item in draft.variants if (ref := scoped_evidence(item.evidence, item.value, item.scope)) is not None
    )
    units = []
    for unit in draft.sample_units:
        details = tuple(
            SampleDetail(label=item.label, value=item.value, evidence=ref)
            for item in unit.details if (ref := evidence(item.evidence, item.value)) is not None
        )
        if details:
            units.append(SampleUnit(role=unit.role, details=details))
    return facts, variants, SampleUsed(units=tuple(units))


def merge_product_info(outputs: list[dict]) -> ProductInfo | None:
    """Merge supported values without inventing cross-dimension combinations."""
    facts: dict[tuple[str, str, str, str], ProductFact] = {}
    variants: dict[tuple[str, str, str], ProductVariant] = {}
    for output in outputs:
        for raw in output.get("facts", []):
            try:
                item = ProductFact.model_validate(raw)
            except ValueError:
                continue
            fact_key = (_normalized(item.group), _normalized(item.label), _compact(item.value), _normalized(item.scope or ""))
            prior = facts.get(fact_key)
            facts[fact_key] = item.model_copy(update={"evidence": prior.evidence + item.evidence}) if prior else item
        for raw in output.get("variants", []):
            try:
                item = ProductVariant.model_validate(raw)
            except ValueError:
                continue
            variant_key = (_normalized(item.dimension), _compact(item.value), _normalized(item.scope or ""))
            prior = variants.get(variant_key)
            variants[variant_key] = item.model_copy(update={"evidence": prior.evidence + item.evidence}) if prior else item
    if not facts and not variants:
        return None
    by_label: dict[tuple[str, str, str], set[str]] = {}
    for item in facts.values():
        by_label.setdefault((_normalized(item.group), _normalized(item.label), _normalized(item.scope or "")), set()).add(_normalized(item.value))
    merged_facts = tuple(
        item.model_copy(update={"conflicting": len(by_label[(_normalized(item.group), _normalized(item.label), _normalized(item.scope or ""))]) > 1})
        for item in facts.values()
    )
    return ProductInfo(facts=merged_facts, variants=tuple(variants.values()))
