"""Independently labelled regressions from the October 3 application audit."""
from __future__ import annotations

import pytest

from app.analysis.grounding import statement_mismatches
from app.analysis.product_info import ProductExtractionDraft, validate_extraction, validate_span_products
from app.analysis.spans import CaptionSpan


CASES = [
    ("Sony WH-1000XM5", "254 g", "WH-1000XM4 weighs 254 g. WH-1000XM5 weighs 250 g.", False),
    ("Sony WH-1000XM5", "250 g", "WH-1000XM4 weighs 254 g. WH-1000XM5 weighs 250 g.", True),
    ("Sony WH-1000XM5", "254 g", "WH-1000XM4 weighs 254 g.", False),
    ("Sony WH-1000XM5", "250 g", "WH–1000XM4 weighs 254 g, while WH 1000 XM 5 weighs 250 g.", True),
    ("Sony WH-1000XM5", "254 g", "254 g for WH-1000XM4; 250 g for WH-1000XM5.", False),
    ("Sony WH-1000XM5", "250 g", "254 g for WH-1000XM4; 250 g for WH-1000XM5.", True),
    ("Sony WH-1000XM5", "254 g", "WH-1000XM50 weighs 254 g.", False),
    ("iPhone 16 Pro Max", "199 g", "iPhone 16 Pro weighs 199 g. iPhone 16 Pro Max weighs 227 g.", False),
    ("iPhone 16 Pro Max", "227 g", "iPhone 16 Pro weighs 199 g. iPhone 16 Pro Max weighs 227 g.", True),
    ("Sony WH-1000XM5", "250 g", "WH-1000XM5 has 250 mAh capacity.", False),
    ("Sony WH-1000XM5", "250 g", "The weight is 250 grams.", True),
    ("iPhone 16 Pro Max", "256 GB", "This review unit has 256 gigabytes of storage.", True),
    ("iPhone 16 Pro Max", "512 GB", "This review unit has 256 gigabytes of storage.", False),
    ("iPhone 16 Pro Max", "6.9 inches", "The screen measures six point nine inches.", True),
    ("iPhone 16 Pro Max", "6.9 inches", "The screen measures six point eight inches.", False),
    ("iPhone 16 Pro Max", "6.9 inches", "The screen measures six or nine inches.", False),
]


@pytest.mark.parametrize("product,value,quote,expected", CASES)
def test_fact_and_numeric_grounding_share_ownership_and_units(product, value, quote, expected):
    transcript = "[1-10] " + quote
    evidence = {"source_part": "transcript", "excerpt": quote, "timestamp_seconds": 1}
    arguments = dict(title=product + " comparison review", description="", transcript_body=transcript,
                     video_id="abcdefghijk", canonical_product=product)
    legacy, _, _ = validate_extraction(ProductExtractionDraft.model_validate({"facts": [{
        "group": "Hardware", "label": "Measurement", "value": value, "evidence": evidence,
    }]}), **arguments)
    current, _, _ = validate_span_products({"facts": [{
        "group": "Hardware", "label": "Measurement", "value": value, "span_refs": ["c1"],
    }]}, {"c1": CaptionSpan(ref="c1", text=quote, start=1, end=10)}, diagnostics=[], **arguments)
    assert bool(legacy) == expected
    assert bool(current) == expected
    assert (not statement_mismatches("Measurement " + value, quote, product)) == expected
    if current:
        assert current[0].evidence[0].excerpt == quote


@pytest.mark.parametrize("quote,expected", [
    ("Available in black and blue.", False),
    ("Another reviewer's unit is blue.", False),
    ("Another reviewer's unit is blue, but my review unit is black.", False),
    ("My review unit is not blue.", False),
    ("My review unit is blue.", True),
    ("We tested the blue WH-1000XM5.", True),
    ("My review unit is blue. Available in black and red as well.", True),
])
def test_sample_requires_reviewer_attribution_in_displayed_citation(quote, expected):
    arguments = dict(title="Sony WH-1000XM5 review", description="", transcript_body="[1-10] " + quote,
                     video_id="abcdefghijk", canonical_product="Sony WH-1000XM5")
    legacy_diagnostics, current_diagnostics = [], []
    _, _, legacy = validate_extraction(ProductExtractionDraft.model_validate({"sample_units": [{
        "role": "Review unit", "details": [{"label": "Color", "value": "blue", "evidence": {
            "source_part": "transcript", "excerpt": quote, "timestamp_seconds": 1,
        }}],
    }]}), diagnostics=legacy_diagnostics, **arguments)
    _, variants, current = validate_span_products({
        "variants": [{"dimension": "Color", "value": "blue", "span_refs": ["c1"]}],
        "sample_units": [{"role": "Review unit", "details": [{
            "label": "Color", "value": "blue", "span_refs": ["c1"],
        }]}],
    }, {"c1": CaptionSpan(ref="c1", text=quote, start=1, end=10)}, diagnostics=current_diagnostics, **arguments)
    assert bool(legacy.units) == expected
    assert bool(current.units) == expected
    if quote.startswith("Available"):
        assert variants
        assert any(item["code"] == "sample_attribution_not_supported" for item in current_diagnostics)
        assert any(item["code"] == "sample_attribution_not_supported" for item in legacy_diagnostics)
