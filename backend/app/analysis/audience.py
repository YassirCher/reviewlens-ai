"""Bounded audience signals tied to distinct comments, kept secondary to reviews."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import Field

from app.analysis.contracts import StrictModel, AudienceAnalysisDraft


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
