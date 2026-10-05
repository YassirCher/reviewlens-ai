"""Bounded audience signals tied to distinct comments, kept secondary to reviews."""
from __future__ import annotations

import re
import copy
import uuid
from datetime import datetime
from typing import Annotated, Literal
from collections.abc import Mapping

from pydantic import Field

from app.analysis.contracts import StrictModel, AudienceAnalysisDraft
from app.analysis.contracts import AudienceAnalystInput
from app.analysis.comment_validation import confident_language, non_latin_language, recurring_support, translation_mismatches


class ClassifiedAudienceInput(AudienceAnalystInput):
    product_name: str = Field(min_length=1, max_length=500)
    translation_language: Literal["en", "fr"] = "en"


class CommentClassification(StrictModel):
    ref: str = Field(min_length=1, max_length=200)
    relevant: bool
    sentiment: Literal["positive", "neutral", "negative"]
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[a-z]{2})?$")
    translation: str | None = Field(default=None, min_length=1, max_length=800)


class RecurringSignal(StrictModel):
    statement: str = Field(min_length=1, max_length=160)
    comment_refs: tuple[str, ...] = Field(min_length=2, max_length=20)


class BoundAudienceDraft(StrictModel):
    positive_pct: int = Field(ge=0, le=100)
    neutral_pct: int = Field(ge=0, le=100)
    negative_pct: int = Field(ge=0, le=100)
    recurring_pros: tuple[RecurringSignal, ...] = Field(default=(), max_length=15)
    recurring_cons: tuple[RecurringSignal, ...] = Field(default=(), max_length=15)
    repeated_issues: tuple[RecurringSignal, ...] = Field(default=(), max_length=15)
    audience_agrees_with_reviewer: bool | None = None
    confidence_score: int = Field(ge=0, le=100)
    sampling_limitations: tuple[str, ...] = Field(default=(), max_length=8)


class CompactRecurringSignal(RecurringSignal):
    statement: str = Field(min_length=1, max_length=120)
    comment_refs: tuple[str, ...] = Field(min_length=2, max_length=2)


class CompactAudienceDraft(BoundAudienceDraft):
    recurring_pros: tuple[CompactRecurringSignal, ...] = Field(default=(), max_length=3)
    recurring_cons: tuple[CompactRecurringSignal, ...] = Field(default=(), max_length=3)
    repeated_issues: tuple[CompactRecurringSignal, ...] = Field(default=(), max_length=3)
    sampling_limitations: tuple[str, ...] = Field(default=(), max_length=3)


class ClassifiedAudienceDraft(StrictModel):
    comments: tuple[CommentClassification, ...] = Field(max_length=20)
    recurring_pros: tuple[CompactRecurringSignal, ...] = Field(default=(), max_length=3)
    recurring_cons: tuple[CompactRecurringSignal, ...] = Field(default=(), max_length=3)
    repeated_issues: tuple[CompactRecurringSignal, ...] = Field(default=(), max_length=3)


class CitedRecurringSignal(CompactRecurringSignal):
    supporting_excerpts: tuple[Annotated[str, Field(min_length=1, max_length=120)], Annotated[str, Field(min_length=1, max_length=120)]] = Field(description="Exact passages in comment_refs order; copy original text or its validated translation.")


class GroundedAudienceDraft(ClassifiedAudienceDraft):
    comments: tuple[CommentClassification, ...] = Field(max_length=12)
    recurring_pros: tuple[CitedRecurringSignal, ...] = Field(default=(), max_length=3)
    recurring_cons: tuple[CitedRecurringSignal, ...] = Field(default=(), max_length=3)
    repeated_issues: tuple[CitedRecurringSignal, ...] = Field(default=(), max_length=3)


class AudienceBindingError(ValueError):
    def __init__(self, total: int):
        super().__init__('audience percentages must sum to 100')
        self.issue = {'path': 'sentiment_percentages', 'type': 'audience_percentage_sum_invalid',
                      'fields': ['positive_pct', 'neutral_pct', 'negative_pct'], 'actual_total': total, 'required_total': 100}


def audience_schema(output_model: type[BoundAudienceDraft]) -> dict:
    schema = output_model.model_json_schema()
    if issubclass(output_model, CompactAudienceDraft):
        schema['properties']['sampling_limitations']['items']['maxLength'] = 160
    return schema


def grounded_classification_schema() -> dict:
    """Request only classifications; optional recurrence must not fail a source."""
    schema = copy.deepcopy(GroundedAudienceDraft.model_json_schema())
    schema['properties'] = {'comments': schema['properties']['comments']}
    schema['required'] = ['comments']
    schema['properties']['comments']['maxItems'] = 8
    return schema


def comment_catalog(context: str, run_at: datetime) -> tuple[str, set[str], set[str]]:
    lines, refs, dates = [], set(), set()
    for original_id, likes, published, text in re.findall(r"^- \[([^\]]+)\] likes=(\d+) published=(\S+): (.+)$", context, re.M):
        if published != "unknown":
            try:
                date = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if date.replace(tzinfo=None) > run_at.replace(tzinfo=None):
                    continue
                dates.add(date.date().isoformat())
            except ValueError:
                continue
        # Repeated retrieval context cannot create a second distinct comment.
        if original_id in refs:
            continue
        refs.add(original_id)
        lines.append(f"- [{original_id}] likes={likes} published={published}: {text}")
    return "\n".join(lines), refs, dates


def original_comments(catalog: str) -> dict[str, str]:
    return {ref: text for ref, text in re.findall(r"^- \[([^\]]+)\] likes=\d+ published=\S+: (.+)$", catalog, re.M)}


def bounded_comment_catalog(context: str, run_at: datetime) -> tuple[str, set[str], dict]:
    """Select complete comments, never prefixes, within the audience output cap."""
    catalog, refs, _ = comment_catalog(context, run_at)
    originals = original_comments(catalog)
    lines = [line for line in catalog.splitlines() if len(originals[re.match(r'- \[([^\]]+)\]', line)[1]]) <= 160][:8]
    selected = '\n'.join(lines)
    selected_refs = set(original_comments(selected))
    return selected, selected_refs, {"comments_supplied": len(selected_refs), "comments_not_selected": len(refs) - len(selected_refs),
        "selection_policy": "complete_comments_8x160_v2"}


def bind_audience(payload: dict, *, source_id: uuid.UUID, sampled: int, refs: set[str],
                  dates: set[str]) -> tuple[AudienceAnalysisDraft, list[dict]]:
    draft = BoundAudienceDraft.model_validate(payload)
    total = draft.positive_pct + draft.neutral_pct + draft.negative_pct
    if total != 100:
        raise AudienceBindingError(total)
    diagnostics = []
    output = draft.model_dump(mode="json")
    for field in ("recurring_pros", "recurring_cons", "repeated_issues"):
        retained = []
        for index, signal in enumerate(getattr(draft, field)):
            selected = set(signal.comment_refs)
            if len(selected) < 2 or not selected <= refs or len(selected) != len(signal.comment_refs):
                diagnostics.append({"path": f"{field}[{index}]", "code": "recurrence_not_supported"})
            else:
                retained.append(signal.statement)
        output[field] = retained
    limitations = []
    for text in draft.sampling_limitations:
        stated_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text))
        if stated_dates - dates:
            diagnostics.append({"path": "sampling_limitations", "code": "audience_date_not_supported"})
        else:
            limitations.append(text)
    output.update(source_id=source_id, comments_sampled=sampled, comments_retained=len(refs),
        sampling_limitations=[f"Sampled {sampled} comments; retained {len(refs)}. This limited sample is not representative of all owners.",
                              "Comments are secondary audience signals, not verified product evidence.", *limitations][:10])
    return AudienceAnalysisDraft.model_validate(output), diagnostics


def bind_classifications(payload: dict, *, source_id: uuid.UUID, sampled: int,
                         refs: set[str], translation_language: str,
                         original_texts: Mapping[str, str] | None = None,
                         product: str = "", grounded: bool = False) -> tuple[AudienceAnalysisDraft | None, dict]:
    """Calculate audience summaries from a complete, reference-bound classification."""
    draft = (GroundedAudienceDraft if grounded else ClassifiedAudienceDraft).model_validate(payload)
    if grounded and (original_texts is None or set(original_texts) != refs):
        raise ValueError("original_comment_coverage_invalid")
    supplied = [item.ref for item in draft.comments]
    if len(set(supplied)) != len(supplied) or set(supplied) != refs:
        raise ValueError("comment_reference_coverage_invalid")
    diagnostics = []
    valid = []
    for item in draft.comments:
        requires_translation = item.language.split("-")[0] not in {"en", "fr"}
        if requires_translation != bool(item.translation):
            raise ValueError("comment_translation_required_or_unexpected")
        if original_texts is not None:
            original = original_texts[item.ref]
            detected = confident_language(original)
            if (detected and detected.split('-')[0] != item.language.split('-')[0]) or (non_latin_language(original) and not requires_translation):
                diagnostics.append({"ref": item.ref, "code": "comment_source_language_mismatch"})
                continue
            if item.translation:
                translated = confident_language(item.translation)
                if (translated and translated != translation_language) or non_latin_language(item.translation):
                    diagnostics.append({"ref": item.ref, "code": "comment_translation_language_mismatch"})
                    continue
                if translated is None:
                    diagnostics.append({"ref": item.ref, "code": "comment_translation_language_uncertain"})
                    continue
                if translation_mismatches(original, item.translation):
                    diagnostics.append({"ref": item.ref, "code": "comment_translation_content_mismatch"})
                    continue
            elif detected is None:
                diagnostics.append({"ref": item.ref, "code": "comment_source_language_uncertain"})
        valid.append(item)
    relevant = {item.ref: item for item in valid if item.relevant}
    signals = {}
    cited_signals = {}
    for field in ("recurring_pros", "recurring_cons", "repeated_issues"):
        retained = []
        for signal in getattr(draft, field):
            selected = set(signal.comment_refs)
            if len(selected) != len(signal.comment_refs) or len(selected) < 2 or not selected <= relevant.keys():
                diagnostics.append({"path": field, "code": "recurrence_not_supported"})
                continue
            expected = "positive" if field == "recurring_pros" else "negative"
            if any(relevant[ref].sentiment != expected for ref in selected):
                diagnostics.append({"path": field, "code": "recurrence_sentiment_mismatch"})
                continue
            if original_texts is not None:
                excerpts = getattr(signal, "supporting_excerpts", ())
                if len(excerpts) != 2 or any(not excerpt.strip() or len(excerpt) > 120 or excerpt not in (
                        original_texts[ref] if relevant[ref].translation is None else relevant[ref].translation)
                        for ref, excerpt in zip(signal.comment_refs, excerpts)) or not recurring_support(signal.statement, excerpts, product):
                    diagnostics.append({"path": field, "code": "recurrence_text_not_supported"})
                    continue
            retained.append(signal)
        signals[field] = [item.statement for item in retained]
        cited_signals[field] = [item.model_dump(mode="json") for item in retained]
    artifact = {"status": "analyzed" if relevant else "insufficient", "comments_sampled": sampled,
        "comments_retained": len(refs), "comments_relevant": len(relevant),
        "comments_translated": sum(bool(item.translation) for item in valid),
        "comments_validated": len(valid), "comments_excluded": len(draft.comments) - len(valid),
        "translation_language": translation_language,
        "classifications": [item.model_dump(mode="json") for item in valid],
        "signals": cited_signals, "binding_diagnostics": diagnostics}
    if not relevant:
        return None, artifact
    counts = [sum(item.sentiment == name for item in relevant.values())
              for name in ("positive", "neutral", "negative")]
    percentages = [100 * count // len(relevant) for count in counts]
    remainder_order = sorted(range(3), key=lambda i: (-(100 * counts[i] % len(relevant)), i))
    for index in remainder_order[:100 - sum(percentages)]:
        percentages[index] += 1
    analysis = AudienceAnalysisDraft(source_id=source_id, comments_sampled=sampled,
        comments_retained=len(refs), positive_pct=percentages[0], neutral_pct=percentages[1],
        negative_pct=percentages[2], **signals, audience_agrees_with_reviewer=None,
        confidence_score=min(60, len(relevant) * 3), sampling_limitations=(
            f"{len(relevant)} relevant comments from {len(refs)} retained and {sampled} sampled; a limited, nonrepresentative sample.",
            "Comments are secondary audience signals, not verified product evidence.",
            *(["Some comments were excluded because their language or translation could not be validated."] if len(valid) < len(draft.comments) else []),
        ))
    return analysis, artifact
