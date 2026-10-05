"""Positive controls and diagnosed failures; no model calls or invented live scores."""
from __future__ import annotations

import uuid
import copy
import html
import io
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from app.analysis.review import bind_review, normalize_usage
from app.analysis.spans import CaptionSpan, bind_span_extraction, owned_use_candidates
from app.analysis.audience import bind_classifications, bounded_comment_catalog
from app.analysis.grounding import statement_mismatches
from app.analysis.support_audit import SupportAuditorInput, SupportedAuditResult, support_audit_input, supported_audit_schema, verified_sources
from app.services.pdf_generator import _sanitize
from app.services.unicode_pdf import report_html, _quote_actual_text
from app.llmops.contracts import strictify_json_schema
from jsonschema import Draft202012Validator


@pytest.mark.parametrize("tail", ["", " I discuss comfort, sound, controls, the carrying case and travel in this review, with further observations."])
def test_ownership_duration_keeps_its_unit_with_a_long_tail(tail):
    phrase = "I've been using Sony's WH-1000XM5 headphones for about 3 months." + tail
    payload = {"review": {"review_type": "long_term", "ownership_context": "owned",
        "reviewer_sentiment_score": 70, "purchase_recommendation_score": 70, "evidence_quality_score": 80,
        "purchase_verdict": "buy_with_caveats", "recommendation_summary": "Extended use is stated.",
        "claims": [{"claim": "The reviewer used these headphones.", "kind": "context", "topic": "usage", "span_refs": ["c1"], "confidence": 80}],
        "ownership_span_refs": ["c1"]}}
    extraction, _ = bind_span_extraction(payload, (CaptionSpan(ref="c1", text=phrase, start=10, end=25),), "Sony WH-1000XM5")
    result, diagnostics = normalize_usage(bind_review(extraction.review, uuid.uuid4()), phrase)
    assert result.usage_period_raw.endswith("3 months")
    assert result.usage_period_days_estimate == 90
    assert result.review_type == "long_term" and diagnostics["extended_use_supported"]


def test_captured_main_device_year_at_zero_is_cited_and_retained():
    phrase = ("16 Pro Max as my main device for the past year. And I want to give my final update on wear and tear, "
              "battery health, charging habits, my overall experience, and what I'm most looking forward to when it comes to the")
    spans = (CaptionSpan(ref='c1', text=phrase, start=0, end=14.799),)
    assert owned_use_candidates(spans) == ('c1',)
    payload = {'review': {'review_type': 'long_term', 'ownership_context': 'owned',
        'reviewer_sentiment_score': 70, 'purchase_recommendation_score': 70, 'evidence_quality_score': 70,
        'purchase_verdict': 'buy_with_caveats', 'recommendation_summary': 'The reviewer describes a year of use.',
        'claims': [{'claim': 'The reviewer used this as their main device for the past year.',
                    'kind': 'context', 'topic': 'usage', 'span_refs': ['c1'], 'confidence': 70}],
        'ownership_span_refs': ['c1']}}
    extraction, _ = bind_span_extraction(payload, spans, 'iPhone 16 Pro Max')
    result, diagnostic = normalize_usage(bind_review(extraction.review, uuid.uuid4()), phrase)
    assert result.usage_period_days_estimate == 365 and diagnostic['extended_use_supported']
    assert result.usage_period_raw in phrase and result.claims[0].evidence[0].timestamp_start_seconds == 0
    assert extraction.product_information is None or not extraction.product_information.sample_units


def comment(ref, language="en", translation=None, sentiment="positive"):
    return dict(ref=ref, relevant=True, language=language, translation=translation, sentiment=sentiment)


@pytest.mark.parametrize("declared,translation,accepted", [
    ("ar", "Le son est bon.", True), ("ar", "The sound is good.", False), ("en", None, False),
])
def test_language_checks_use_original_text(declared, translation, accepted):
    result, artifact = bind_classifications({"comments": [comment("a", declared, translation)]},
        source_id=uuid.uuid4(), sampled=1, refs={"a"}, translation_language="fr",
        original_texts={"a": "الصوت جيد"}, grounded=True)
    assert bool(result) is accepted
    assert artifact["comments_excluded"] == int(not accepted)


def test_bounded_comment_selection_preserves_whole_originals_and_counts():
    lines = [f'- [c{i}] likes=0 published=unknown: Great battery lasts for a full day.' for i in range(20)]
    lines.insert(0, '- [long] likes=10 published=unknown: ' + 'Longer comment with full context. ' * 20)
    body, refs, selection = bounded_comment_catalog('\n'.join(lines), datetime.now(timezone.utc))
    assert len(refs) == 8 and 'long' not in refs
    assert selection['comments_not_selected'] == 13
    assert all(line in '\n'.join(lines) for line in body.splitlines())


def test_comment_dates_are_bounded_by_immutable_acquisition_time():
    admitted = datetime(2026, 10, 4, tzinfo=timezone.utc)
    acquired = admitted + timedelta(minutes=2)
    posted_during_run = admitted + timedelta(minutes=1)
    future = acquired + timedelta(minutes=1)
    lines = (f'- [valid] likes=0 published={posted_during_run.isoformat()}: The sound is good.\n'
             f'- [future] likes=0 published={future.isoformat()}: The sound is good.')
    selected, refs, _ = bounded_comment_catalog(lines, acquired)
    assert refs == {'valid'} and 'future' not in selected


@pytest.mark.parametrize("translation,accepted", [
    ("The battery does not last 2 days.", True), ("The battery lasts 2 days.", False),
    ("The battery does not last 40 days.", False),
])
def test_translation_preserves_explicit_numbers_and_negation(translation, accepted):
    result, _ = bind_classifications({"comments": [comment("a", "es", translation, "negative")]},
        source_id=uuid.uuid4(), sampled=1, refs={"a"}, translation_language="en",
        original_texts={"a": "La batería no dura 2 días."}, grounded=True)
    assert bool(result) is accepted


@pytest.mark.parametrize("statement,second,accepted", [
    ("Battery lasts two days.", "My battery lasts two days.", True),
    ("Battery lasts 40 days.", "The screen looks nice.", False),
])
def test_recurrence_requires_both_original_passages(statement, second, accepted):
    originals = {"a": "Battery lasts two days for me.", "b": second}
    payload = {"comments": [comment("a"), comment("b")], "recurring_pros": [{
        "statement": statement, "comment_refs": ["a", "b"], "supporting_excerpts": list(originals.values())}]}
    result, artifact = bind_classifications(payload, source_id=uuid.uuid4(), sampled=2,
        refs=set(originals), original_texts=originals, translation_language="en", grounded=True)
    assert result and bool(result.recurring_pros) is accepted
    assert len(artifact["classifications"]) == 2


def corpus():
    return json.loads((Path(__file__).parent / 'fixtures/quality_citations.json').read_text(encoding='utf-8'))['cases']


@pytest.mark.parametrize("case", [case for case in corpus() if case['kind'] == 'central_finding'])
def test_captured_central_positive_and_negative_controls(case):
    product = 'iPhone 16 Pro Max' if case['case'] == 'iphone' else 'Sony WH-1000XM5'
    assert bool(statement_mismatches(case['statement'], ' '.join(case['cited_excerpts']), product)) is (not case['expected_supported'])


@pytest.mark.parametrize("case", [case for case in corpus() if case['kind'] == 'source_claim' and case['adjudication'] == 'supported'])
def test_adjudicated_supported_source_controls_survive(case):
    product = 'iPhone 16 Pro Max' if case['case'] == 'iphone' else 'Sony WH-1000XM5'
    assert not statement_mismatches(case['statement'], ' '.join(case['cited_excerpts']), product)


def audit_fixture():
    value = json.loads((Path(__file__).parent / 'fixtures/blackshark_audit_format.json').read_text(encoding='utf-8'))
    supplied = SupportAuditorInput.model_validate(support_audit_input({'report_draft': value['draft'], 'source_analyses': value['source_analyses']}))
    response = {'decisions': {path: {'supported': True, 'category': None, 'explanation': None,
        'rejected_part_ref': None, 'supporting_parts': {part.part_ref: [citation.evidence_ref for citation in finding.citations]
        for part in finding.statement}} for path, finding in supplied.all_findings().items()}, 'other_issues': []}
    return value, supplied, response


def test_exhaustive_source_audit_requires_all_positive_anchors():
    _, supplied, response = audit_fixture()
    schema = Draft202012Validator(strictify_json_schema(supported_audit_schema(supplied)))
    assert not list(schema.iter_errors(response))
    _, diagnostics = SupportedAuditResult.model_validate(response).as_audit(supplied)
    assert diagnostics['support_coverage'] == len(supplied.all_findings())
    first = next(iter(response['decisions']))
    for mutation in ('missing', 'foreign', 'missing_part'):
        bad = copy.deepcopy(response)
        if mutation == 'missing': del bad['decisions'][first]
        elif mutation == 'foreign':
            part = next(iter(bad['decisions'][first]['supporting_parts']))
            bad['decisions'][first]['supporting_parts'][part] = ['e99999']
        else: bad['decisions'][first]['supporting_parts'] = {}
        with pytest.raises(ValueError):
            SupportedAuditResult.model_validate(bad).as_audit(supplied)


def test_pruned_source_prose_cannot_keep_unverified_claims():
    fixture, _, _ = audit_fixture()
    original = copy.deepcopy(fixture['source_analyses'])
    for item in original:
        item['evidence_quality_score'] = 80
    review = original[0]
    review['claims'].append({**copy.deepcopy(review['claims'][0]), 'claim': 'Uncited retailer return instructions.', 'central': False})
    review['recommendation_summary'] = 'Uncited retailer return instructions.'
    retained, diagnostic = verified_sources(original, [f"source_analyses[0].claims[{len(review['claims']) - 1}]"], [])
    assert retained and 'Uncited retailer' not in json.dumps(retained)
    assert original[0]['recommendation_summary'] == 'Uncited retailer return instructions.'
    assert diagnostic['source_claims_removed'] == 1


@pytest.mark.parametrize('case', [case for case in corpus() if case['kind'] == 'source_claim' and case['adjudication'] == 'unsupported'])
def test_captured_rejections_remove_claim_and_uncited_source_prose(case):
    fixture, _, _ = audit_fixture()
    reviews = copy.deepcopy(fixture['source_analyses'])
    for review in reviews:
        review['evidence_quality_score'] = 80
    # Prescribed labels verify the actual audit->projection contract. They do
    # not assert that a live model will produce the prescribed decisions.
    reviews[0]['claims'][0]['claim'] = case['statement']
    reviews[0]['recommendation_summary'] = case['statement']
    quotes = reviews[0]['claims'][0]['evidence']
    for index, quote in enumerate(quotes):
        quote['evidence_text'] = case['cited_excerpts'][min(index, len(case['cited_excerpts']) - 1)]
    supplied = SupportAuditorInput.model_validate(support_audit_input({'report_draft': fixture['draft'], 'source_analyses': reviews}))
    response = {'decisions': {path: {'supported': True, 'category': None, 'explanation': None,
        'rejected_part_ref': None, 'supporting_parts': {part.part_ref: [citation.evidence_ref for citation in finding.citations]
        for part in finding.statement}} for path, finding in supplied.all_findings().items()}, 'other_issues': []}
    path = 'source_analyses[0].claims[0]'
    response['decisions'][path] = {'supported': False, 'category': 'material', 'explanation': 'Displayed quotation lacks the labelled material detail.',
        'rejected_part_ref': supplied.all_findings()[path].statement[0].part_ref, 'supporting_parts': {}}
    _, diagnostics = SupportedAuditResult.model_validate(response).as_audit(supplied)
    retained, _ = verified_sources(reviews, diagnostics['source_claim_rejections'], [])
    assert case['statement'] not in json.dumps(retained, ensure_ascii=False)


@pytest.mark.parametrize('text', ['La qualité est très bonne.', 'अच्छी बैटरी', 'البطارية جيدة', '电池很好', '<script> & quoted'])
def test_pdf_preserves_unicode_and_escapes_markup(text):
    assert html.unescape(_sanitize(text)) == text
    page = report_html({'product_name': text, 'summary': text}, 'token')
    assert html.escape(text) in page and '<script>' not in page


def tagged_quote_fixture(include_text=True):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, ArrayObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    reference = page.indirect_reference
    text = DictionaryObject({NameObject('/Type'): NameObject('/StructElem'), NameObject('/S'): NameObject('/NonStruct'),
        NameObject('/Pg'): reference, NameObject('/K'): NumberObject(1)})
    quote = DictionaryObject({NameObject('/Type'): NameObject('/StructElem'), NameObject('/S'): NameObject('/BlockQuote'),
        NameObject('/Pg'): reference, NameObject('/K'): ArrayObject([NumberObject(0)] + ([writer._add_object(text)] if include_text else []))})
    writer.root_object[NameObject('/StructTreeRoot')] = DictionaryObject({NameObject('/K'): writer._add_object(quote)})
    stream = DecodedStreamObject()
    stream.set_data(b'/BlockQuote << /MCID 0 >> BDC 0 0 10 10 re f EMC '
                    b'/NonStruct << /MCID 1 >> BDC BT /F1 12 Tf (Original battery quote) Tj ET EMC')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_logical_text_binds_glyphs_and_preserves_painted_content():
    from pypdf import PdfReader
    from pypdf.generic import ContentStream
    original = tagged_quote_fixture()
    changed = _quote_actual_text(original, ['Original battery quote'])
    before, after = PdfReader(io.BytesIO(original)), PdfReader(io.BytesIO(changed))
    old_ops = ContentStream(before.pages[0].get_contents(), before).operations
    new_ops = ContentStream(after.pages[0].get_contents(), after).operations
    painted = lambda ops: [(args, op) for args, op in ops if op not in (b'BDC', b'BMC', b'EMC')]
    assert painted(old_ops) == painted(new_ops)
    owner_index = next(i for i, (args, op) in enumerate(new_ops) if op == b'BDC' and args[1].get('/MCID') == 1)
    assert new_ops[owner_index + 1][0][1]['/ActualText'] == 'Original battery quote'
    box_index = next(i for i, (args, op) in enumerate(new_ops) if op == b'BDC' and args[1].get('/MCID') == 0)
    assert new_ops[box_index + 1][1] == b're'


@pytest.mark.parametrize('include_text,quotes', [(False, ['Original battery quote']), (True, [])])
def test_pdf_rejects_unbound_logical_quote_replacements(include_text, quotes):
    with pytest.raises(RuntimeError, match='quotation'):
        _quote_actual_text(tagged_quote_fixture(include_text), quotes)
