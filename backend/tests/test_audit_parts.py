"""Actual failed draft plus labelled responses; fixtures do not establish model accuracy."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.analysis.audit import AuditDecisionError, DecisionAuditorInput, FindingAuditResult, decision_audit_input
from app.analysis.audit_parts import PartAuditorInput, ReferencedAuditResult, part_audit_input, referenced_audit_schema
from app.analysis.audit_parts import OwnedAuditResult
from app.analysis.rendering import CompleteBuyingSynthesis
from app.analysis.contracts import FinalReportDraft
from app.analysis.grounding import ground_report
from app.analysis.registry import AGENT_REGISTRY, snapshot_input_model, snapshot_output_model
from app.analysis.rendering import DistinctBuyingSynthesis, NormalizedBuyingSynthesis
from app.analysis.synthesis import evidence_catalog
from app.llmops.contracts import strictify_json_schema


def captured():
    return json.loads((Path(__file__).parent / 'fixtures/blackshark_audit_format.json').read_text(encoding='utf-8'))


def supplied(fixture):
    return PartAuditorInput.model_validate(part_audit_input({'report_draft': fixture['draft'], 'source_analyses': fixture['source_analyses']}))


def decisions(value):
    return {'finding_checks': [{'field_path': f.field_path, 'supported': True,
        'evidence_refs': [q.evidence_ref for q in f.citations], 'category': None,
        'rejected_part_ref': None, 'explanation': None}
        for f in (*value.report_draft.consensus_pros, *value.report_draft.consensus_cons)], 'other_issues': []}


def test_actual_failed_draft_has_eight_identical_unfinished_strengths():
    fixture = captured()
    assert len(fixture['source_analyses']) == 5
    pros = fixture['draft']['consensus_pros']
    assert len(pros) == 8 and all(p == pros[0] for p in pros)
    assert pros[0]['statement'].endswith('and no audible')


def test_parts_are_lossless_bounded_and_reject_without_retyping_text():
    fixture = captured()
    fixture['draft']['summary'] = 'Exact spacing  and punctuation. ' * 40 + 'বাংলা উদ্ধৃতি'
    value = supplied(fixture)
    restored, parts = value.binding()
    assert restored == DecisionAuditorInput.model_validate(decision_audit_input({'report_draft': fixture['draft'], 'source_analyses': fixture['source_analyses']}))
    assert all(0 < len(text) <= 200 for text, _ in parts.values())
    response = decisions(value)
    first = response['finding_checks'][0]
    first.update(supported=False, category='material',
        rejected_part_ref=value.report_draft.consensus_pros[0].statement[0].part_ref,
        explanation='The assertion ends mid-sentence and describes several reviewers using one owner.')
    audit, diagnostic = ReferencedAuditResult.model_validate(response).as_audit(value)
    assert audit.verdict == 'fail'
    assert diagnostic['rejections'][0]['unsupported_clause'] in restored.report_draft.consensus_pros[0].statement
    assert diagnostic['selected_parts'][0]['field_path'] == first['field_path']


@pytest.mark.parametrize('mutation', ['unknown_part', 'foreign_part', 'missing', 'duplicate', 'wrong_path', 'foreign_quote', 'pass_with_part'])
def test_part_format_preserves_coverage_and_ownership(mutation):
    value = supplied(captured())
    response = decisions(value)
    first = response['finding_checks'][0]
    if mutation in {'unknown_part', 'foreign_part'}:
        first.update(supported=False, category='material', explanation='Unsupported clause.',
                     rejected_part_ref='p99999' if mutation == 'unknown_part' else value.report_draft.consensus_cons[0].statement[0].part_ref)
    elif mutation == 'missing': response['finding_checks'].pop()
    elif mutation == 'duplicate': response['finding_checks'].append(copy.deepcopy(first))
    elif mutation == 'wrong_path': first['field_path'] = 'report_draft.consensus_pros[99]'
    elif mutation == 'foreign_quote':
        first['evidence_refs'] = [next(ref for check in response['finding_checks'] for ref in check['evidence_refs']
                                      if ref not in first['evidence_refs'])]
    else: first['rejected_part_ref'] = value.report_draft.consensus_pros[0].statement[0].part_ref
    with pytest.raises((ValueError, ValidationError)):
        ReferencedAuditResult.model_validate(response).as_audit(value)


def test_narrative_parts_cannot_reject_foreign_fields():
    value = supplied(captured())
    response = decisions(value)
    response['other_issues'] = [{'code': 'unsupported_narrative', 'field_path': 'report_draft.summary',
        'evidence_refs': [], 'rejected_part_ref': value.report_draft.summary[0].part_ref,
        'explanation': 'This is a labelled negative narrative fixture.'}]
    assert ReferencedAuditResult.model_validate(response).as_audit(value)[0].verdict == 'fail'
    response['other_issues'][0]['rejected_part_ref'] = value.report_draft.consensus_cons[0].statement[0].part_ref
    with pytest.raises(AuditDecisionError, match='foreign rejection part'):
        ReferencedAuditResult.model_validate(response).as_audit(value)


def test_generation_schema_accepts_correct_shape_and_rejects_unknown_parts():
    value = supplied(captured())
    schema = strictify_json_schema(referenced_audit_schema(value))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    response = decisions(value)
    validator.validate(response)
    response['finding_checks'][0].update(supported=False, category='material', rejected_part_ref='p99999', explanation='Unknown part.')
    assert list(validator.iter_errors(response))


def test_empty_finding_contract_remains_valid_for_legacy_adapter_calls():
    fixture = captured()
    fixture['draft']['consensus_pros'] = fixture['draft']['consensus_cons'] = []
    value = supplied(fixture)
    schema = strictify_json_schema(referenced_audit_schema(value))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(decisions(value))


def test_disagreement_rejection_is_bound_to_its_side():
    fixture = captured()
    fixture['draft']['disagreements'] = [{'topic': 'Fit', 'side_a': 'Comfortable fit', 'side_b': 'Uncomfortable fit',
        'side_a_source_ids': [fixture['source_analyses'][0]['source_id']],
        'side_b_source_ids': [fixture['source_analyses'][1]['source_id']]}]
    value = supplied(fixture); response = decisions(value)
    disagreement = value.report_draft.disagreements[0]
    issue = {'code': 'unsupported_disagreement', 'field_path': 'report_draft.disagreements[0].side_a',
             'evidence_refs': [disagreement.side_a_citations[0].evidence_ref],
             'rejected_part_ref': disagreement.side_a[0].part_ref, 'explanation': 'Labelled semantic rejection.'}
    response['other_issues'] = [issue]
    assert ReferencedAuditResult.model_validate(response).as_audit(value)[0].verdict == 'fail'
    issue['rejected_part_ref'] = disagreement.side_b[0].part_ref
    with pytest.raises(AuditDecisionError):
        ReferencedAuditResult.model_validate(response).as_audit(value)


def synthesis(fixture):
    _, bindings, _ = evidence_catalog(fixture['source_analyses'])
    reverse = {eid: ref for ref, (_, eid) in bindings.items()}
    original = fixture['draft']['consensus_pros'][0]
    attribute, observation = original['statement'].split(': ', 1)
    return {'summary': 'Cited reviewer observations.', 'assertions': [
        {'kind': 'strength', 'attribute': attribute, 'observation': observation,
         'evidence_refs': [reverse[eid] for eid in original['evidence_node_ids']]} for _ in range(8)]}


def test_duplicate_removal_preserves_legacy_and_records_original_indices():
    fixture = captured(); response = synthesis(fixture)
    old = NormalizedBuyingSynthesis.model_validate(response).as_report('Blackshark T11', 'Blackshark T11', fixture['source_analyses'])
    new, diagnostic = DistinctBuyingSynthesis.model_validate(response).compile_report('Blackshark T11', 'Blackshark T11', fixture['source_analyses'])
    assert len(old.consensus_pros) == 8 and len(new.consensus_pros) == 1
    assert [d['loc'] for d in diagnostic if d['type'] == 'duplicate_assertion'] == [['assertions', i] for i in range(1, 8)]
    assert new.consensus_pros[0] == old.consensus_pros[0]


@pytest.mark.parametrize('difference', ['owner', 'kind', 'condition', 'observation'])
def test_distinct_source_opposing_or_conditional_findings_remain(difference):
    fixture = captured(); response = synthesis(fixture)
    response['assertions'] = response['assertions'][:2]
    item = response['assertions'][1]
    if difference == 'owner':
        _, bindings, _ = evidence_catalog(fixture['source_analyses'])
        owner = bindings[item['evidence_refs'][0]][0]
        item['evidence_refs'] = [next(ref for ref, (sid, _) in bindings.items() if sid != owner)]
    elif difference == 'kind': item['kind'] = 'caveat'
    elif difference == 'condition': item['conditions'] = 'A distinct condition for independent audit.'
    else: item['observation'] = 'A distinct assertion for independent audit.'
    draft = DistinctBuyingSynthesis.model_validate(response).as_report('Blackshark T11', 'Blackshark T11', fixture['source_analyses'])
    assert len(draft.consensus_pros) + len(draft.consensus_cons) == 2


def test_all_rejections_block_publication_and_preserve_bounded_repair():
    fixture = captured(); value = supplied(fixture); response = decisions(value)
    for check, finding in zip(response['finding_checks'], (*value.report_draft.consensus_pros, *value.report_draft.consensus_cons)):
        check.update(supported=False, category='material', rejected_part_ref=finding.statement[0].part_ref, explanation='Labelled rejection exercising fail-closed repair.')
    audit = ReferencedAuditResult.model_validate(response).as_audit(value)[0]
    safe, result, terminal = ground_report(FinalReportDraft.model_validate(fixture['draft']), fixture['source_analyses'], audit, strict_grounding=True)
    assert result.verdict == 'fail' and not terminal
    assert not safe.consensus_pros and not safe.consensus_cons


def test_successor_and_legacy_contracts_are_explicit():
    assert AGENT_REGISTRY['quality_auditor'].output_model is OwnedAuditResult
    assert AGENT_REGISTRY['consensus_analyst'].output_model is CompleteBuyingSynthesis
    assert snapshot_output_model('quality_auditor', ReferencedAuditResult.model_json_schema()) is ReferencedAuditResult
    assert snapshot_output_model('consensus_analyst', DistinctBuyingSynthesis.model_json_schema()) is DistinctBuyingSynthesis
    assert snapshot_input_model('quality_auditor', DecisionAuditorInput.model_json_schema()) is DecisionAuditorInput
    assert snapshot_output_model('quality_auditor', FindingAuditResult.model_json_schema()) is FindingAuditResult
    assert snapshot_output_model('consensus_analyst', NormalizedBuyingSynthesis.model_json_schema()) is NormalizedBuyingSynthesis
