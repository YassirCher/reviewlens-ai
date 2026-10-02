"""Matched, independently labelled offline cases against eda2ada. No paid calls.

Reconstructed responses are synthetic. Timings measure local application work,
not provider latency. Full changed prompt/schema/output costs are estimated.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.analysis.registry import AGENT_REGISTRY
from app.analysis.audit_parts import PartAuditorInput, part_audit_input, owned_audit_schema, referenced_audit_schema
from app.analysis.contracts import AuditResult, FinalReportDraft
from app.analysis.grounding import ground_report, statement_mismatches
from app.analysis.prompting import build_prompt_envelope
from app.analysis.product_info import ProductExtractionDraft, validate_extraction, validate_span_products
from app.analysis.review import ClassifiedVideoExtraction
from app.analysis.spans import caption_spans, span_extraction_schema
from app.analysis.rendering import prioritized_synthesis_input
from app.analysis.synthesis import evidence_bound_synthesis_schema
from app.knowledge.retrieval import estimate_tokens
from tests.test_evidence_binding import QUANTITY_CASES


def baseline(name, path, replacements=()):
    code = subprocess.run(['git', 'show', f'eda2ada:{path}'], cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8').stdout
    for old, new in replacements:
        code = code.replace(old, new)
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(code, path, 'exec'), module.__dict__)
    return module


def cost(spec, task, context, output, schema, envelope_builder):
    envelope = envelope_builder(spec, task_instruction=spec.purpose, task_input=task,
        context_manifest_id=None, rendered_context=context)
    return sum(estimate_tokens(text) for text in (envelope.system, envelope.user,
        json.dumps(schema, separators=(',', ':')), json.dumps(output, separators=(',', ':'))))


def main():
    baseline('binding_old_quantities', 'backend/app/analysis/quantities.py')
    old_ground = baseline('binding_old_ground', 'backend/app/analysis/grounding.py',
        [('from app.analysis.quantities import', 'from binding_old_quantities import')])
    old_product = baseline('binding_old_product', 'backend/app/analysis/product_info.py')
    old_registry = baseline('binding_old_registry', 'backend/app/analysis/registry.py')
    old_prompt = baseline('binding_old_prompt', 'backend/app/analysis/prompting.py')
    fixture = json.loads((ROOT / 'backend/tests/fixtures/live_binding_cases.json').read_text())
    rows = []
    for index, (label, statement, quote, expected) in enumerate(QUANTITY_CASES):
        started = time.perf_counter()
        run = fixture['runs'][index % 2]
        reviews = copy.deepcopy(run['reviews'])
        source = reviews[0]
        first = source['claims'][0]['evidence'][0]
        first.update(evidence_text=quote, timestamp_start_seconds=10, timestamp_end_seconds=20)
        source['claims'] = [{'claim': statement, 'central': True, 'evidence': [first]}]
        item = {'statement': statement, 'source_ids': [source['source_id']], 'evidence_node_ids': [first['evidence_node_id']]}
        draft = FinalReportDraft(product_display_name='Black Shark T11', product_canonical_name='Black Shark T11',
            summary='Cited review findings.', consensus_pros=(item,))
        product_value = statement.split(' ', 1)[1] if ' ' in statement else statement
        product_raw = {'facts': [{'group': 'Observed detail', 'label': label, 'value': product_value,
            'scope': 'Black Shark T11', 'evidence': {'source_part': 'transcript', 'excerpt': quote, 'timestamp_seconds': 10}}]}
        matched = {}
        for changed in (False, True):
            clock_start = time.perf_counter()
            registry = AGENT_REGISTRY if changed else old_registry.AGENT_REGISTRY
            builder = build_prompt_envelope if changed else old_prompt.build_prompt_envelope
            matcher = statement_mismatches if changed else old_ground.statement_mismatches
            accepted = not matcher(statement, quote, 'Black Shark T11')
            product_module = sys.modules['app.analysis.product_info'] if changed else old_product
            facts, _, _ = product_module.validate_extraction(product_module.ProductExtractionDraft.model_validate(product_raw),
                title='Black Shark T11 review', description='', transcript_body=f'[10-20] {quote}',
                video_id='abcdefghijk', canonical_product='Black Shark T11')
            if changed:
                selected_spans = caption_spans(f'[10-20] {quote}', 'Black Shark T11')
                facts, _, _ = validate_span_products({'facts': [{'group': 'Observed detail', 'label': label,
                    'value': product_value, 'scope': 'Black Shark T11',
                    'span_refs': [span.ref for span in selected_spans[:2]]}]},
                    {span.ref: span for span in selected_spans}, title='Black Shark T11 review', description='',
                    transcript_body=f'[10-20] {quote}', video_id='abcdefghijk',
                    canonical_product='Black Shark T11', diagnostics=[])
            tokens = 2000  # Two unchanged planning/curation calls, equal in both arms.
            for review in reviews:
                data = copy.deepcopy({key: value for key, value in review.items() if key in ClassifiedVideoExtraction.model_fields['review'].annotation.model_fields})
                for claim in data['claims']:
                    claim.update(kind='strength', topic='Reviewer observation')
                    claim['evidence'] = [{key: value for key, value in evidence.items() if key not in {'source_node_id', 'evidence_node_id'}} for evidence in claim['evidence']]
                context = '\n'.join(f'[{e["timestamp_start_seconds"]}-{e["timestamp_end_seconds"]}] {e["evidence_text"]}'
                    for c in review['claims'] for e in c['evidence'])
                task = {'source_id': review['source_id'], 'transcript_node_id': '00000000-0000-0000-0000-000000000001',
                    'source_title': 'Black Shark T11 review', 'channel_id': review['channel_id'],
                    'transcript_language': review['transcript_language'], 'translated': review['translated'], 'caption_kind': review['caption_kind']}
                output = {'review': data, 'product_information': product_raw if review is source else None}
                schema = registry['review_analyst'].output_model.model_json_schema()
                if changed:
                    task['canonical_product'] = 'Black Shark T11'
                    spans = caption_spans(context, 'Black Shark T11')
                    context = '\n'.join(json.dumps(s.model_dump(mode='json'), separators=(',', ':')) for s in spans)
                    for claim in data['claims']:
                        selected = [s.ref for s in spans if any(e['evidence_text'] in s.text for e in claim['evidence'])][:2]
                        claim.pop('evidence')
                        claim.update(span_refs=selected or ['c1'], confidence=90)
                    data['ownership_span_refs'] = []
                    output['product_information'] = {'facts': [{'group': 'Observed detail', 'label': label,
                        'value': product_value, 'scope': 'Black Shark T11', 'span_refs': ['c1']}]} if review is source else None
                    schema = span_extraction_schema(spans, registry["review_analyst"].output_model)
                tokens += cost(registry['review_analyst'], task, context, output, schema, builder)
            raw = {'product_display_name': 'Black Shark T11', 'product_canonical_name': 'Black Shark T11', 'requested_source_count': 5,
                'source_analyses': reviews, 'audience_analyses': []}
            supplied = prioritized_synthesis_input(raw)
            synthesis_output = {'summary': 'Cited review findings.', 'assertions': [{'kind': 'strength', 'attribute': label,
                'observation': statement, 'evidence_refs': ['e1']}]}
            synthesis_cost = cost(registry['consensus_analyst'], supplied, '<no-authorized-context />', synthesis_output,
                evidence_bound_synthesis_schema(registry['consensus_analyst'].input_model.model_validate(supplied)), builder)
            tokens += synthesis_cost
            audit = PartAuditorInput.model_validate(part_audit_input({'report_draft': draft.model_dump(mode='json'), 'source_analyses': reviews}))
            decision = {'supported': True, 'category': None, 'rejected_part_ref': None, 'explanation': None}
            audit_output = {'decisions': {'report_draft.consensus_pros[0]': decision}, 'other_issues': []} if changed else {
                'finding_checks': [{**decision, 'field_path': 'report_draft.consensus_pros[0]', 'evidence_refs': ['e1']}], 'other_issues': []}
            audit_cost = cost(registry['quality_auditor'], audit.model_dump(mode='json'), '<no-authorized-context />', audit_output,
                owned_audit_schema(audit) if changed else referenced_audit_schema(audit), builder)
            tokens += audit_cost
            # Two explicit reference-fault replays reproduce the application defect;
            # their actual discarded model output is unavailable, so these are synthetic.
            retry = int(not changed and index in (0, 1))
            tokens += retry * audit_cost
            # Exercise bounded repair accounting with a failed final audit in both arms.
            repairs = int(index == len(QUANTITY_CASES) - 1)
            tokens += repairs * (synthesis_cost + audit_cost + 300)
            # Five audience calls have changed prompts/schema/output and are all charged.
            comments = bool(index % 2)
            if comments:
                audience_input = {'source_id': source['source_id'], 'comment_set_node_id': '00000000-0000-0000-0000-000000000002',
                    'comments_sampled': 20, 'comments_retained': 20}
                output = {'positive_pct': 50, 'neutral_pct': 25, 'negative_pct': 25, 'confidence_score': 20,
                    'audience_agrees_with_reviewer': None, 'recurring_pros': [], 'recurring_cons': [], 'repeated_issues': [], 'sampling_limitations': []}
                if not changed: output.update(audience_input)
                tokens += 5 * cost(registry['audience_analyst'], audience_input, 'Captured comment context cost: ' + 'x' * 2000,
                    output, registry['audience_analyst'].output_model.model_json_schema(), builder)
            matched['candidate' if changed else 'baseline'] = {'estimated_total_tokens': tokens,
                'calls': 9 + 5 * comments + retry + 2 * repairs, 'validation_retries': retry,
                'correction_calls': repairs, 'reaudit_calls': repairs,
                'valid_finding_retention': int(accepted and expected), 'unsupported_retention': int(accepted and not expected),
                'valid_fact_retention': int(bool(facts) and expected), 'source_coverage': len(reviews),
                'topic_coverage': int(accepted and expected), 'local_ms': (time.perf_counter() - clock_start) * 1000}
        assert matched['candidate']['valid_finding_retention'] >= matched['baseline']['valid_finding_retention']
        assert matched['candidate']['unsupported_retention'] <= matched['baseline']['unsupported_retention']
        assert matched['candidate']['valid_fact_retention'] >= matched['baseline']['valid_fact_retention']
        assert bool(matched['candidate']['valid_finding_retention']) == expected
        rows.append({'label': label, 'independent_supported_label': expected, 'synthetic_response': True, **matched})
    totals = {arm: sum(row[arm]['estimated_total_tokens'] for row in rows) for arm in ('baseline', 'candidate')}
    result = {'baseline_commit': 'eda2ada', 'passed': totals['candidate'] <= totals['baseline'], 'matched_cases': len(rows),
        'estimated_total_tokens': totals, 'valid_facts': {arm: sum(r[arm]['valid_fact_retention'] for r in rows) for arm in totals},
        'valid_findings': {arm: sum(r[arm]['valid_finding_retention'] for r in rows) for arm in totals},
        'unsupported_retention': {arm: sum(r[arm]['unsupported_retention'] for r in rows) for arm in totals},
        'local_p95_ms': {arm: sorted(r[arm]['local_ms'] for r in rows)[-2] for arm in totals},
        'measurement': 'Changed prompts, schema, selected context and synthetic outputs; all retries and bounded repair charged. Planning cost held equal.',
        'coverage_measurement': 'Captured source inputs held equal; topic retention measured for each independently labelled assertion. Not generated whole-report quality.',
        'production_parity_verified': False, 'live_accuracy_verified': False, 'paid_calls': 0, 'cases': rows}
    output = ROOT / '.audit-cache/evidence-binding-replays.json'
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != 'cases'}, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
