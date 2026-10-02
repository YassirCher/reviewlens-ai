"""Caption selections are owned by the server, not copied by a language model.

The catalog is built only from the context packet already selected for this task.
No additional retrieval or translation is performed.
"""
from __future__ import annotations

import copy
import re
from typing import Literal

from pydantic import Field, ValidationError

from app.analysis.contracts import StrictModel, ReviewAnalystInput
from app.analysis.product_info import ProductExtractionDraft, contains_source_instruction
from app.analysis.review import ClassifiedReview, ClassifiedVideoExtraction
from app.analysis.quantities import product_passage, product_mentions


class CaptionSpan(StrictModel):
    ref: str
    text: str = Field(min_length=3, max_length=300)
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class CaptionBindingError(ValueError):
    def __init__(self, path: list, refs: tuple[str, ...]):
        super().__init__("unknown, foreign, or duplicate caption reference")
        self.issue = {"loc": path, "type": "caption_reference_invalid", "ownership": "assigned_catalog_mismatch",
                      "reference_ids": [ref[:40] for ref in refs[:2]]}


def caption_spans(context: str, product: str = "") -> tuple[CaptionSpan, ...]:
    segments = sorted({(float(a), float(b), text) for a, b, text in re.findall(
        r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] (.+)$", context, re.M)})
    groups: list[tuple[float, float, str]] = []
    mentions = [owned for _, _, text in segments for _, _, owned in product_mentions(text, product)] if product else []
    # A missing requested-name mention is not proof of a comparison. Automatic
    # captions can misrecognize a model code; explicit foreign facts still fail
    # independent scope validation. Do not discard all neutral review passages.
    comparison = any(mentions) and not all(mentions)
    active_owned = not comparison
    previous_end: float | None = None
    joinable = False

    def append(start: float, end: float, text: str) -> None:
        nonlocal joinable
        # Original character order and caption offsets remain intact.
        pieces = re.findall(r".{1,300}(?:\s|$)|\S{1,300}", text)
        for piece in pieces:
            piece = piece.strip()
            if len(piece) < 3:
                continue
            if joinable and groups and start >= groups[-1][1] - .001 and end - groups[-1][0] <= 45 and len(groups[-1][2]) + len(piece) + 1 <= 300:
                previous = groups.pop()
                groups.append((previous[0], end, previous[2] + " " + piece))
            else:
                groups.append((start, end, piece))
            joinable = True

    for start, end, text in segments:
        if end < start or end - start > 45 or contains_source_instruction(text):
            joinable = False
            continue
        if previous_end is not None and start - previous_end > 45:
            active_owned = not comparison
            joinable = False
        previous_end = end
        if not comparison:
            append(start, end, text)
            continue
        cursor = 0
        for a, _, owned in product_mentions(text, product) if product else ():
            if active_owned:
                append(start, end, text[cursor:a])
            else:
                joinable = False
            if active_owned != owned:
                joinable = False
            cursor, active_owned = a, owned
        if active_owned:
            append(start, end, text[cursor:])
        else:
            joinable = False
    return tuple(CaptionSpan(ref=f"c{index + 1}", start=a, end=b, text=text)
                 for index, (a, b, text) in enumerate(groups))


class SpanReviewInput(ReviewAnalystInput):
    canonical_product: str = Field(min_length=1, max_length=200)


class SpanClaim(StrictModel):
    claim: str = Field(min_length=1, max_length=240)
    central: bool = False
    kind: Literal["strength", "caveat", "context"]
    topic: str = Field(min_length=1, max_length=40)
    span_refs: tuple[str, ...] = Field(min_length=1, max_length=2)
    confidence: int = Field(ge=0, le=100)


class SpanReview(ClassifiedReview):
    claims: tuple[SpanClaim, ...] = Field(min_length=1, max_length=6)
    ownership_span_refs: tuple[str, ...] = Field(default=(), max_length=2)


class SpanFact(StrictModel):
    group: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=160)
    scope: str | None = Field(default=None, max_length=160)
    span_refs: tuple[str, ...] = Field(min_length=1, max_length=2)


class SpanVariant(StrictModel):
    dimension: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=160)
    scope: str | None = Field(default=None, max_length=160)
    span_refs: tuple[str, ...] = Field(min_length=1, max_length=2)


class SpanDetail(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=160)
    span_refs: tuple[str, ...] = Field(min_length=1, max_length=2)


class SpanUnit(StrictModel):
    role: str = Field(default="Review unit", min_length=1, max_length=80)
    details: tuple[SpanDetail, ...] = Field(default=(), max_length=12)


class SpanProducts(StrictModel):
    facts: tuple[SpanFact, ...] = Field(default=(), max_length=24)
    variants: tuple[SpanVariant, ...] = Field(default=(), max_length=24)
    sample_units: tuple[SpanUnit, ...] = Field(default=(), max_length=4)


class SpanVideoExtraction(StrictModel):
    review: SpanReview
    product_information: SpanProducts | None = None


class CompactSpanReview(SpanReview):
    recommendation_summary: str = Field(min_length=1, max_length=320)
    pros: tuple[str, ...] = Field(default=(), max_length=3)
    cons: tuple[str, ...] = Field(default=(), max_length=3)
    major_issues: tuple[str, ...] = Field(default=(), max_length=3)
    recommended_for: tuple[str, ...] = Field(default=(), max_length=3)
    not_recommended_for: tuple[str, ...] = Field(default=(), max_length=3)
    limitations: tuple[str, ...] = Field(default=(), max_length=3)


class CompactSpanUnit(SpanUnit):
    details: tuple[SpanDetail, ...] = Field(default=(), max_length=3)


class CompactSpanProducts(SpanProducts):
    facts: tuple[SpanFact, ...] = Field(default=(), max_length=6)
    variants: tuple[SpanVariant, ...] = Field(default=(), max_length=3)
    sample_units: tuple[CompactSpanUnit, ...] = Field(default=(), max_length=1)


class CompactSpanVideoExtraction(SpanVideoExtraction):
    review: CompactSpanReview
    product_information: CompactSpanProducts | None = None


def span_extraction_schema(catalog: tuple[CaptionSpan, ...], output_model: type[SpanVideoExtraction] = SpanVideoExtraction) -> dict:
    schema = copy.deepcopy(output_model.model_json_schema())
    for name in ("SpanClaim", "SpanFact", "SpanVariant", "SpanDetail"):
        schema["$defs"][name]["properties"]["span_refs"]["items"] = {"type": "string", "enum": [s.ref for s in catalog]}
    review_name = "CompactSpanReview" if issubclass(output_model, CompactSpanVideoExtraction) else "SpanReview"
    review_schema = schema["$defs"][review_name]["properties"]
    review_schema["ownership_span_refs"]["items"] = {"type": "string", "enum": [s.ref for s in catalog]}
    if issubclass(output_model, CompactSpanVideoExtraction):
        for name in ('pros', 'cons', 'major_issues', 'recommended_for', 'not_recommended_for', 'limitations'):
            review_schema[name]['items']['maxLength'] = 120
    return schema


def bind_span_extraction(payload: dict, catalog: tuple[CaptionSpan, ...], product: str = "") -> tuple[ClassifiedVideoExtraction, list[dict]]:
    # Optional details cannot invalidate the review, including malformed sections.
    review = SpanReview.model_validate(payload.get("review"))
    try:
        products = SpanProducts.model_validate(payload.get("product_information"))
    except ValidationError:
        products = None
    spans = {span.ref: span for span in catalog}
    diagnostics: list[dict] = []

    def resolve(refs: tuple[str, ...], path: list | None = None) -> list[CaptionSpan]:
        if len(set(refs)) != len(refs) or any(ref not in spans for ref in refs):
            raise CaptionBindingError(path or [], refs)
        return [spans[ref] for ref in refs]

    raw = review.model_dump(mode="json")
    raw.pop("ownership_span_refs")
    for index, (claim, selected) in enumerate(zip(raw["claims"], review.claims)):
        claim.pop("span_refs")
        confidence = claim.pop("confidence")
        claim["evidence"] = [{"evidence_text": s.text, "timestamp_start_seconds": s.start,
            "timestamp_end_seconds": s.end, "confidence": confidence, "support_type": "supports"}
            for s in resolve(selected.span_refs, ['review', 'claims', index, 'span_refs'])]
    ownership = resolve(review.ownership_span_refs, ['review', 'ownership_span_refs'])
    phrase = " ".join(s.text for s in ownership)
    if product:
        phrase = product_passage(phrase, product).strip()
    duration = re.search(r"\b(?:\d+|past|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:days?|weeks?|months?|years?)\b", phrase, re.I)
    # Preserve the exact cited ownership passage around its duration, never an estimate.
    start = max(0, duration.start() - 65) if duration else 0
    if start and start < len(phrase) and not phrase[start - 1].isspace():
        boundary = phrase.find(' ', start)
        start = boundary + 1 if boundary >= 0 else start
    selected_phrase = phrase[start:start + 120]
    if start + 120 < len(phrase):
        selected_phrase = selected_phrase.rsplit(' ', 1)[0]
    raw.update(usage_period_mentioned=bool(phrase), usage_period_raw=selected_phrase if phrase else None,
               usage_period_days_estimate=None)
    details = {"facts": [], "variants": [], "sample_units": []}

    def item(raw: dict, path: str) -> list[dict]:
        refs = tuple(raw.pop("span_refs"))
        try:
            selected = resolve(refs)
        except ValueError:
            diagnostics.append({"path": path, "code": "caption_reference_invalid", "label": raw.get("label", raw.get("dimension")),
                                "value": raw["value"], "scope": raw.get("scope"), "reference_ids": list(refs)})
            return []
        # Multiple selected spans keep their own exact timestamps and excerpts.
        return [{**raw, "evidence": {"source_part": "transcript", "excerpt": s.text, "timestamp_seconds": s.start}}
                for s in selected]

    if products:
        for field in ("facts", "variants"):
            for index, fact in enumerate(getattr(products, field)):
                details[field].extend(item(fact.model_dump(mode="json"), f"{field}[{index}]"))
        for index, unit in enumerate(products.sample_units):
            details["sample_units"].append({"role": unit.role, "details": [bound
                for j, detail in enumerate(unit.details)
                for bound in item(detail.model_dump(mode="json"), f"sample_units[{index}].details[{j}]")]})
    # Legacy projection accepts at most 24 items; the model still proposes at most 24 facts.
    details["facts"] = details["facts"][:24]
    details["variants"] = details["variants"][:24]
    for unit in details["sample_units"]:
        unit["details"] = unit["details"][:12]
    result = ClassifiedVideoExtraction(review=ClassifiedReview.model_validate(raw),
        product_information=ProductExtractionDraft.model_validate(details) if products else None)
    object.__setattr__(result, "_span_product_payload", products.model_dump(mode="json") if products else None)
    object.__setattr__(result, "_caption_spans", spans)
    return result, diagnostics
