"""Bounded, source-grounded product details extracted from selected YouTube videos."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.product_attributes import canonical_fact, canonical_variant_dimension
from app.tools.evidence import transcript_excerpt_matches
from app.analysis.quantities import explicit_quantities, product_passage, without_product_identity, product_mentions


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
    r"\b(?:ignore|disregard|override)\b.{0,80}\b(?:instructions?|prompts?|system|developer)\b"
    r"|\b(?:reveal|print|expose)\b.{0,80}\b(?:secrets?|keys?|prompts?)\b",
    re.IGNORECASE,
)


def contains_source_instruction(text: str) -> bool:
    return bool(_SOURCE_INSTRUCTION.search(text))


_DETAIL_CUE = re.compile(
    r"\b(?:has|have|includes?|comes? with|features?|supports?|rated|weighs?|measures?|"
    r"available|offered|options?|variants?|colors?|colours?|sizes?|materials?|capacities?)\b",
    re.IGNORECASE,
)
_MEASUREMENT = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mm|cm|m|kg|g|lb|oz|ml|l|gb|tb|mah|wh|w|hz|khz|"
    r"mp|fps|hours?|minutes?|watts?|inches?|%|°c|°f)\b",
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


def _same_model_words(first: str, second: str) -> bool:
    """Recognize an exact brand/model token reorder, never a partial family."""
    left = re.findall(r"[a-z]+|\d+", first.casefold())
    right = re.findall(r"[a-z]+|\d+", second.casefold())
    return len(left) >= 3 and any(token.isdigit() for token in left) and sorted(left) == sorted(right)


def _value_key(value: str) -> str:
    # Preserve decimal points and separators: 1.86 x 3.2 must not equal 18.6 x 32.
    return "".join(value.casefold().split())


def _detail_score(body: str) -> int:
    """Rank stored chunks by product-like statements, excluding timestamp digits."""
    score = 0
    for line in body.splitlines():
        segment = _SEGMENT.match(line)
        if not segment:
            continue
        spoken = segment.group(3)
        score += 2 * bool(_MEASUREMENT.search(spoken)) + bool(_DETAIL_CUE.search(spoken))
    return min(score, 40)


def select_product_chunk_indexes(bodies: list[str], costs: list[int], budget: int, max_extra: int) -> tuple[int, ...]:
    """Keep the first chunk, then use the same context budget across useful, distant spans."""
    if not bodies:
        return ()
    selected = [0]
    remaining = budget - costs[0]
    while len(selected) - 1 < max_extra:
        choices = [index for index in range(1, len(bodies)) if index not in selected and costs[index] <= remaining]
        if not choices:
            break
        best = max(
            choices,
            key=lambda index: (
                _detail_score(bodies[index]) * 2
                + 12 * min(abs(index - chosen) for chosen in selected) / len(bodies),
                min(abs(index - chosen) for chosen in selected),
                -index,
            ),
        )
        selected.append(best)
        remaining -= costs[best]
    return tuple(selected)


def _different_model(scope: str | None, excerpt: str, canonical_product: str | None) -> bool:
    if not canonical_product:
        return False
    if scope and _same_model_words(scope, canonical_product):
        return False
    if not product_passage(excerpt, canonical_product).strip():
        return True
    target = re.findall(r"[a-z0-9]+", canonical_product.casefold())
    if len(target) < 2:
        return False
    variant_suffixes = {"pro", "max", "plus", "ultra", "mini", "lite", "se", "xl"}
    anchor = list(target)
    if any(any(char.isdigit() for char in word) for word in anchor):
        while len(anchor) > 2 and not any(char.isdigit() for char in anchor[-1]) and anchor[-1] not in variant_suffixes:
            anchor.pop()
    if not scope:
        family = "".join(anchor[:-1])
        spoken = _compact(excerpt)
        return len(family) >= 6 and family in spoken and "".join(anchor) not in spoken
    scoped = re.findall(r"[a-z0-9]+", scope.casefold())
    compact_target = _compact(canonical_product)
    compact_scope = _compact(scope)
    if compact_target in compact_scope:
        return False
    shared = set(target) & set(scoped)
    if not shared:
        return False  # A region or other non-model qualifier may have no shared words.
    variant_suffix = target[-1] in variant_suffixes
    if variant_suffix and target[-1] not in scoped and len(shared) >= 2:
        return True
    target_codes = {word for word in target if any(char.isdigit() for char in word)}
    scoped_codes = {word for word in scoped if any(char.isdigit() for char in word)}
    if len(shared) >= 2 and scoped_codes - target_codes:
        return True
    target_core = "".join(anchor)
    return not (target_core in compact_scope or compact_scope in compact_target) and len(shared) >= 2


def _scope_qualifier(scope: str | None, canonical_product: str) -> str:
    value = (scope or '').strip()
    if canonical_product and _same_model_words(value, canonical_product):
        return ''
    # These are identity labels, not assertions about a variant or region.
    if value.casefold() in {'product', 'model', 'this product', 'this model', 'requested product'}:
        return ''
    if value and any(owned for _, _, owned in product_mentions(value, canonical_product)):
        mentions = product_mentions(value, canonical_product)
        if len(mentions) == 1 and value == value[mentions[0][0]:mentions[0][1]]:
            return ''
    return without_product_identity(value, canonical_product).strip()


def _supported_value(value: str, excerpt: str) -> bool:
    # Captions often transcribe ownership as "Intel's Core" or "Nvidia's RTX".
    # Remove only that grammatical suffix; numeric and model-code checks below
    # still require the exact value in the cited passage.
    excerpt = re.sub(r"(?<=\w)[’']s\b", "", excerpt)
    quantities, remaining = explicit_quantities(value)
    if quantities:
        quoted, _ = explicit_quantities(excerpt)
        if not quantities <= quoted:
            return False
        # Unit conversion does not exempt unrelated words in the proposed value.
        residue = re.sub(r"[\s,;:()]+", "", remaining)
        if not residue:
            return True
    literal = re.search(r"(?<!\w)" + re.escape(_normalized(value)) + r"(?!\w)", _normalized(excerpt))
    if literal is not None:
        return True
    if not any(char.isdigit() for char in value):
        return False
    # Allow punctuation and spacing changes (5,000 mAh / 5000mAh) without
    # accepting 5000 as evidence for 15000 or 50000.
    return re.search(r"(?<!\d)" + re.escape(_compact(value)) + r"(?!\d)", _compact(excerpt)) is not None


def _sample_attribution(value: str, excerpt: str, canonical_product: str | None) -> bool:
    """The displayed citation must state the reviewer's tested configuration."""
    passage = product_passage(excerpt, canonical_product or "")
    clauses = re.split(r"(?<=[.!?;])\s+|,(?=\s)|\b(?:but|while|whereas)\s+", passage, flags=re.I)
    cue = re.compile(
        r"\b(?:(?:my|our|this)\s+(?:(?:review|test|tested)\s+)?(?:unit|sample|configuration)|"
        r"(?:I|we)\s+(?:tested|reviewed|used|am\s+using|are\s+using|have\s+been\s+using)|"
        r"(?:unit|configuration|model)\s+(?:I|we)\s+(?:tested|reviewed|used|am\s+using|are\s+using))\b", re.I,
    )
    for clause in clauses:
        match = cue.search(clause)
        if not match:
            continue
        attributed = clause[match.start():]
        if re.search(r"\b(?:available|offered|options?|could|would|might|not|never)\b", attributed, re.I):
            continue
        if _supported_value(value, attributed):
            return True
    return False


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
        if not transcript_excerpt_matches(
            transcript_body, draft.excerpt, timestamp, None, minimum_words=1,
        ):
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
    canonical_product: str | None = None,
    diagnostics: list[dict[str, str]] | None = None,
) -> tuple[tuple[ProductFact, ...], tuple[ProductVariant, ...], SampleUsed]:
    def evidence(item: EvidenceDraft, value: str) -> ProductEvidence | None:
        return validate_evidence(
            item, value=value, title=title, description=description,
            transcript_body=transcript_body, video_id=video_id,
        )

    def scoped_evidence(item: EvidenceDraft, value: str, scope: str | None,
                        path: str, label: str = "") -> ProductEvidence | None:
        code = None
        identity_scope = _scope_qualifier(scope, canonical_product or "")
        identity_supported = bool(canonical_product and (
            _compact(canonical_product) in _compact(title + " " + description)
            or (scope and _same_model_words(scope, canonical_product)
                and _compact(scope) in _compact(title + " " + description))))
        if scope and not (identity_supported and not identity_scope) and _compact(identity_scope or scope) not in _compact(item.excerpt):
            code = "scope_not_supported"
        elif _different_model(scope, item.excerpt, canonical_product):
            code = "sibling_model"
        elif _SOURCE_INSTRUCTION.search(item.excerpt):
            code = "source_instruction"
        elif not _supported_value(value, product_passage(item.excerpt, canonical_product or "")):
            code = "value_not_supported"
        ref = evidence(item, value) if code is None else None
        if ref is None and diagnostics is not None and len(diagnostics) < 96:
            diagnostics.append({"path": path, "code": code or "quote_or_timestamp_mismatch",
                "label": label[:80], "value": value[:160], "scope": scope,
                "reference_ids": [f"transcript@{item.timestamp_seconds}"]})
        return ref

    def reject_comparison_identity(item: FactDraft, index: int) -> bool:
        # A comparison's identity is not a detail of the requested product,
        # even when the model omits its scope and the quote is verbatim.
        label = item.label.casefold().replace("_", " ").replace("-", " ")
        comparison = bool(re.search(r"\b(?:compared|comparison|sibling|other)\b.*\b(?:product|model)\b", label))
        if comparison and diagnostics is not None and len(diagnostics) < 96:
            diagnostics.append({"path": f"facts[{index}]", "code": "sibling_model"})
        return comparison

    facts = tuple(
        ProductFact(group=item.group, label=item.label, value=item.value,
                    scope=_scope_qualifier(item.scope, canonical_product or '') or None, evidence=(ref,))
        for index, item in enumerate(draft.facts)
        if (ref := scoped_evidence(item.evidence, item.value, item.scope, f"facts[{index}]", item.label)) is not None
        and not reject_comparison_identity(item, index)
    )
    variants = tuple(
        ProductVariant(dimension=item.dimension, value=item.value,
                       scope=_scope_qualifier(item.scope, canonical_product or '') or None, evidence=(ref,))
        for index, item in enumerate(draft.variants)
        if (ref := scoped_evidence(item.evidence, item.value, item.scope, f"variants[{index}]", item.dimension)) is not None
    )
    units = []
    for unit_index, unit in enumerate(draft.sample_units):
        details = []
        for index, item in enumerate(unit.details):
            path = f"sample_units[{unit_index}].details[{index}]"
            ref = scoped_evidence(item.evidence, item.value, None, path)
            if ref is not None and _sample_attribution(item.value, ref.excerpt, canonical_product):
                details.append(SampleDetail(label=item.label, value=item.value, evidence=ref))
            elif ref is not None and diagnostics is not None and len(diagnostics) < 96:
                diagnostics.append({"path": path, "code": "sample_attribution_not_supported",
                                    "label": item.label[:80], "value": item.value[:160]})
        if details:
            units.append(SampleUnit(role=unit.role, details=tuple(details)))
    return facts, variants, SampleUsed(units=tuple(units))


def canonicalize_product_info(info: ProductInfo) -> ProductInfo:
    """Unify equivalent facts while retaining every distinct value and citation."""
    facts: dict[tuple[str, str, str], ProductFact] = {}
    variants: dict[tuple[str, str, str], ProductVariant] = {}
    for item in info.facts:
        identity, group, label = canonical_fact(item.group, item.label, item.value)
        fact_key = (identity, _value_key(item.value), _normalized(item.scope or ""))
        prior = facts.get(fact_key)
        evidence = _distinct_evidence((prior.evidence if prior else ()) + item.evidence)
        facts[fact_key] = (prior or item).model_copy(update={"group": group, "label": label, "evidence": evidence})
    for item in info.variants:
        dimension = canonical_variant_dimension(item.dimension)
        variant_key = (_normalized(dimension), _value_key(item.value), _normalized(item.scope or ""))
        prior = variants.get(variant_key)
        evidence = _distinct_evidence((prior.evidence if prior else ()) + item.evidence)
        variants[variant_key] = (prior or item).model_copy(update={"dimension": dimension, "evidence": evidence})
    by_identity: dict[tuple[str, str], set[str]] = {}
    for identity, value, scope in facts:
        by_identity.setdefault((identity, scope), set()).add(value)
    return info.model_copy(update={
        "facts": tuple(item.model_copy(update={"conflicting": len(by_identity[(identity, scope)]) > 1})
                       for (identity, _, scope), item in facts.items()),
        "variants": tuple(variants.values()),
    })


def merge_product_info(outputs: list[dict]) -> ProductInfo | None:
    """Merge supported values without inventing cross-dimension combinations."""
    facts: list[ProductFact] = []
    variants: list[ProductVariant] = []
    for output in outputs:
        for raw in output.get("facts", []):
            try:
                item = ProductFact.model_validate(raw)
            except ValueError:
                continue
            facts.append(item)
        for raw in output.get("variants", []):
            try:
                item = ProductVariant.model_validate(raw)
            except ValueError:
                continue
            variants.append(item)
    if not facts and not variants:
        return None
    return canonicalize_product_info(ProductInfo(facts=tuple(facts), variants=tuple(variants)))


def validate_span_products(payload: dict, spans: dict, *, title: str, description: str,
        transcript_body: str, video_id: str, canonical_product: str, diagnostics: list[dict]):
    """Validate one proposed item across its selected quotes, keeping every citation.

    Each original quote is validated independently; semantic value/scope checks
    use only that item's selected, same-source passages.
    """
    def selected(item: dict, path: str):
        refs = item['span_refs']
        reason = None
        if len(refs) != len(set(refs)) or any(ref not in spans for ref in refs):
            reason = 'caption_reference_invalid'
            quotes = []
        else:
            quotes = [spans[ref] for ref in refs]
        combined = ' '.join(s.text for s in quotes)
        scope = item.get('scope')
        qualifier = _scope_qualifier(scope, canonical_product)
        identity_bound = (_compact(canonical_product) in _compact(title + ' ' + description)
                          or bool(scope and _same_model_words(scope, canonical_product)
                                  and _compact(scope) in _compact(title + ' ' + description)))
        if reason is None:
            if scope and not (identity_bound and not qualifier) and _compact(qualifier or scope) not in _compact(combined):
                reason = 'scope_not_supported'
            elif _different_model(scope, combined, canonical_product):
                reason = 'sibling_model'
            elif _SOURCE_INSTRUCTION.search(combined):
                reason = 'source_instruction'
            elif not _supported_value(item['value'], product_passage(combined, canonical_product)):
                reason = 'value_not_supported'
        evidence = []
        if reason is None:
            for span in quotes:
                bound = validate_evidence(EvidenceDraft(source_part='transcript', excerpt=span.text,
                    timestamp_seconds=span.start), value=span.text, title=title, description=description,
                    transcript_body=transcript_body, video_id=video_id)
                if bound is None:
                    reason = 'quote_or_timestamp_mismatch'
                    break
                evidence.append(bound)
        if reason:
            if len(diagnostics) < 96:
                diagnostics.append({'path': path, 'label': item.get('label', item.get('dimension', '')),
                    'value': item['value'], 'scope': scope, 'reference_ids': refs, 'code': reason})
            return None
        return tuple(evidence)

    facts, variants, units = [], [], []
    for field, target, model in (('facts', facts, ProductFact), ('variants', variants, ProductVariant)):
        for index, item in enumerate(payload.get(field, [])):
            evidence = selected(item, f'{field}[{index}]')
            if evidence:
                values = {key: value for key, value in item.items() if key != 'span_refs'}
                values['scope'] = _scope_qualifier(item.get('scope'), canonical_product) or None
                label = item.get('label', '').casefold()
                if re.search(r'\b(?:compared|comparison|sibling|other)\b.*\b(?:product|model)\b', label.replace('_', ' ')):
                    diagnostics.append({'path': f'{field}[{index}]', 'label': label, 'value': item['value'],
                        'scope': item.get('scope'), 'reference_ids': item['span_refs'], 'code': 'sibling_model'})
                else:
                    target.append(model(**values, evidence=evidence))
    for index, unit in enumerate(payload.get('sample_units', [])):
        details = []
        for j, item in enumerate(unit.get('details', [])):
            evidence = selected(item, f'sample_units[{index}].details[{j}]')
            if evidence:
                # Stable public sample detail has one citation. The value itself
                # must be supported by that displayed quotation.
                primary = next((e for e in evidence if _sample_attribution(item['value'], e.excerpt, canonical_product)), None)
                if primary:
                    details.append(SampleDetail(label=item['label'], value=item['value'], evidence=primary))
                elif len(diagnostics) < 96:
                    diagnostics.append({'path': f'sample_units[{index}].details[{j}]',
                        'label': item['label'], 'value': item['value'], 'reference_ids': item['span_refs'],
                        'code': 'sample_attribution_not_supported'})
        if details:
            units.append(SampleUnit(role=unit['role'], details=tuple(details)))
    return tuple(facts), tuple(variants), SampleUsed(units=tuple(units))


def _distinct_evidence(items: tuple[ProductEvidence, ...]) -> tuple[ProductEvidence, ...]:
    return tuple({(item.video_id, item.source_part, item.timestamp_seconds, _normalized(item.excerpt)): item
                  for item in items}.values())
