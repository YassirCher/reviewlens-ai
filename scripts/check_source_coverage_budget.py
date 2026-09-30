"""Twenty matched offline workflow replays; no network, database, or paid inference.

Measures application prompt/completion estimates and in-process completion time
with a fixed local provider delay. It does not establish live provider parity.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.analysis.audit import (
    DecisionAuditorInput,
    FindingAuditResult,
    decision_audit_input,
)
from app.analysis.contracts import (
    AuditResult,
    ConsensusAnalystInput,
    FinalReportDraft,
    QualityAuditorInput,
    SourceAnalysis,
)
from app.analysis.grounding import ground_report
from app.analysis.product_info import (
    ProductExtractionDraft,
    validate_extraction,
)
from app.analysis.projection import project_claims
from app.analysis.prompting import build_prompt_envelope
from app.analysis.registry import AGENT_REGISTRY, LEGACY_REVIEW_SPEC
from app.analysis.review import (
    bind_review,
    parse_video_extraction,
    video_extraction_schema,
)
from app.analysis.synthesis import evidence_catalog, repair_synthesis_input
from app.knowledge.retrieval import estimate_tokens
from app.llmops.gateway import validated_chat_content
from app.tools.caption_cache import caption_from_node
from app.tools.evidence import transcript_excerpt_matches
from app.tools.youtube import source_slot_queues
from check_report_correction_budget import fixture_audit_response
from compare_buying_report_budget import compare

TOPICS = (
    "Battery lasted thirty hours under light use.",
    "Sound remained clear during quiet indoor listening.",
    "Microphone calls sounded muffled in heavy wind.",
    "The reviewer used these headphones for six months.",
    "The fit stayed comfortable during two hour sessions.",
    "The reviewer recommends them for quiet indoor listening.",
)

LEGACY_CONSENSUS = replace(AGENT_REGISTRY["consensus_analyst"],
    input_model=ConsensusAnalystInput, output_model=FinalReportDraft,
    role_prompt="Summarize purchase-relevant agreement and opposing reviewer claims. Cite every finding from each named source; ground buyer fit and summary in those findings. Keep only material disagreements. Do not calculate score, verdict, or confidence.")
LEGACY_AUDITOR = replace(AGENT_REGISTRY["quality_auditor"], input_model=QualityAuditorInput, output_model=AuditResult, role_prompt=(
    "Check findings, disagreements, summary, buyer fit, and stated duration against cited claims and excerpts. "
    "Flag unsupported meaning, numbers, negation, or model scope with precise paths and codes "
    "unsupported_finding, unsupported_disagreement, or unsupported_narrative. "
    "A source ID alone is insufficient. Return typed issues, never a replacement report."))


def _review(source_id, compact):
    claims = []
    for index in range(6 if compact else 10):
        topic = index % 6
        quote = TOPICS[topic]
        if not compact:
            quote += " The reviewer repeated this test under the same stated conditions with this sample."
        evidence = {"evidence_text": quote, "timestamp_start_seconds": topic * 15,
                    "timestamp_end_seconds": topic * 15 + 5, "confidence": 90, "support_type": "supports"}
        if not compact:
            evidence["source_node_id"] = str(source_id)
        claims.append({"claim": TOPICS[topic], "central": True, "evidence": [evidence]})
    review = {"review_type": "long_term", "ownership_context": "owned",
        "usage_period_mentioned": True, "usage_period_raw": "six months", "usage_period_days_estimate": 180,
        "reviewer_sentiment_score": 75, "purchase_recommendation_score": 75, "evidence_quality_score": 90,
        "purchase_verdict": "buy_with_caveats", "recommendation_summary": "Battery endurance with outdoor call caveats.",
        "pros": [TOPICS[0], TOPICS[1], TOPICS[4]], "cons": [TOPICS[2]], "major_issues": [],
        "recommended_for": ["Quiet indoor listening"], "not_recommended_for": ["Windy outdoor calls"],
        "claims": claims, "limitations": ["Transcript-only evidence"]}
    if not compact:
        review["source_id"] = str(source_id)
    return review


async def replay(case, compact):
    ledger = {"total_tokens": 0, "model_call_count": 0}

    async def invoke(spec, payload, context, output):
        if spec.input_model is DecisionAuditorInput:
            payload = decision_audit_input(payload)
        validated = spec.input_model.model_validate(payload).model_dump(mode="json")
        prompt = build_prompt_envelope(spec, task_instruction=spec.purpose, task_input=validated,
            context_manifest_id=None, rendered_context=context)
        prompt_tokens = estimate_tokens(prompt.system) + estimate_tokens(prompt.user)
        assert prompt_tokens <= spec.max_input_tokens
        schema = video_extraction_schema() if compact and spec.key == "review_analyst" else spec.output_model.model_json_schema()
        if spec.output_model is FindingAuditResult:
            output = fixture_audit_response(validated, output)
        ledger["total_tokens"] += (prompt_tokens + estimate_tokens(json.dumps(schema, separators=(",", ":")))
                                  + estimate_tokens(json.dumps(output, separators=(",", ":"))))
        ledger["model_call_count"] += 1
        await asyncio.sleep(.002)  # Fixed fixture provider delay; never a remote request.
        cleaned, errors = validated_chat_content(SimpleNamespace(response_schema=schema,
            optional_output_fields=("product_information",) if compact and spec.key == "review_analyst" else ()), output)
        assert not errors
        result = spec.output_model.model_validate(cleaned)
        return result.as_audit(DecisionAuditorInput.model_validate(validated))[0] if isinstance(result, FindingAuditResult) else result

    ids = [uuid.uuid5(uuid.NAMESPACE_URL, f"coverage:{case}:{index}") for index in range(5)]
    video_ids = [f"case{case:02d}0000{index}" for index in range(5)]
    candidates = {video: {"channel_id": f"independent-{index}", "deterministic_score": .9 - index * .01,
                          "deterministic_exclusion": None} for index, video in enumerate(video_ids)}
    decisions = [{"video_id": video, "eligible": True, "classification": "long_term",
        "product_relevance": .95, "review_intent": .9, "independence": .9, "evidence_potential": .9,
        "reason_codes": []} for video in video_ids]
    title = f"Test headphones {case:02d}"
    start = time.perf_counter()
    await invoke(AGENT_REGISTRY["research_coordinator"], {"product_name": title, "requested_source_count": 5,
        "requested_language": "en", "analyze_comments": False}, "", {"canonical_label": title, "aliases": [],
        "queries": [title + " review"], "exclusion_hints": [], "requested_source_count": 5,
        "requested_language": "en", "analyze_comments": False})
    await invoke(AGENT_REGISTRY["source_curator"], {"canonical_product": title, "candidates": [{
        "video_id": video, "title": title, "channel_id": candidates[video]["channel_id"],
        "channel_title": "Independent reviewer", "duration_seconds": 600, "view_count": 1000,
        "caption_available": True, "deterministic_score": .9, "deterministic_exclusion": None}
        for video in video_ids]}, "", {"decisions": decisions, "ordered_video_ids": video_ids})
    queues = source_slot_queues(video_ids, candidates, decisions, 5,
                               frozenset(video_ids) if compact else frozenset())
    assert len({queue[0] for queue in queues}) == 5

    async def analyze(index):
        source_id, video_id = ids[index], video_ids[index]
        transcript = "\n".join(f"[{i * 15}-{i * 15 + 5}] " + TOPICS[i % 6]
            + " The reviewer repeated this test under the same stated conditions with this sample."
            + " These observations describe the reviewed sample under the stated conditions and do not establish different configurations."
            for i in range(36 + case % 7))
        metadata = {"video_id": video_id, "title": title, "description": "Battery lasted thirty hours under light use."}
        context = json.dumps(metadata) + "\n" + transcript
        task_input = {"source_id": str(source_id), "transcript_node_id": str(uuid.uuid5(source_id, "transcript")),
            "source_title": title, "channel_id": f"independent-{index}", "transcript_language": "en",
            "translated": False, "caption_kind": "automatic"}
        details = {"facts": [{"group": "Power", "label": "Battery runtime", "value": "thirty hours",
                   "evidence": {"source_part": "description", "excerpt": metadata["description"]}}],
                   "variants": [], "sample_units": []}
        if compact:
            # Rehydrate typed cached captions without copying old node identities.
            cached = caption_from_node(transcript, {"video_id": video_id, "source_language": "en",
                "delivered_language": "en", "caption_kind": "automatic", "translated": False}, video_id)
            assert cached.segments
            extracted = await invoke(AGENT_REGISTRY["review_analyst"], task_input, context,
                                    {"review": _review(source_id, True), "product_information": details})
            draft = bind_review(parse_video_extraction(extracted.model_dump(mode="json")).review, source_id)
            product = ProductExtractionDraft.model_validate(details)
        else:
            # Reproduce the screenshot's two blocked slots and one incorrect evidence identity.
            if index in (1, 3):
                return None
            review = _review(source_id, False)
            if index == 4:
                for claim in review["claims"]:
                    claim["evidence"][0]["source_node_id"] = str(uuid.uuid5(source_id, "chunk"))
            draft, product = await asyncio.gather(
                invoke(LEGACY_REVIEW_SPEC, task_input, context + "\n" + transcript + "\nForeign video metadata",
                       review),
                invoke(AGENT_REGISTRY["product_information_analyst"], {
                    "canonical_product": title, "source_id": str(source_id),
                    "transcript_node_id": task_input["transcript_node_id"]}, context, details))
        validate_extraction(product, title=title, description=metadata["description"], transcript_body=transcript,
                            video_id=video_id, canonical_product=title)
        valid_claims = []
        for claim in draft.claims:
            refs = [quote for quote in claim.evidence if quote.source_node_id == source_id and
                transcript_excerpt_matches(transcript, quote.evidence_text,
                    quote.timestamp_start_seconds, quote.timestamp_end_seconds)]
            if refs:
                valid_claims.append({"claim": claim.claim, "central": claim.central, "evidence": [
                    {**quote.model_dump(mode="json"), "evidence_node_id": str(uuid.uuid5(source_id, claim.claim))}
                    for quote in refs]})
        if not any(claim["central"] for claim in valid_claims):
            return None
        payload = {**draft.model_dump(mode="json"), "claims": valid_claims, "source_score": 75,
            "source_analysis_node_id": str(uuid.uuid5(source_id, "analysis")), "channel_id": f"independent-{index}",
            "transcript_language": "en", "translated": False, "caption_kind": "automatic"}
        return SourceAnalysis.model_validate(payload).model_dump(mode="json")

    reviews = [review for review in await asyncio.gather(*(analyze(i) for i in range(5))) if review]
    evidence_ids = [review["claims"][0]["evidence"][0]["evidence_node_id"] for review in reviews]
    if compact:
        project_claims(reviews)
    else:
        await invoke(AGENT_REGISTRY["knowledge_curator"], {"source_analyses": reviews, "audience_analyses": []}, "",
            {"findings": [{"statement": TOPICS[0], "source_ids": [review["source_id"] for review in reviews],
                           "evidence_node_ids": evidence_ids, "confidence": 85, "relation": "consensus"}]})
    consensus_input = {"product_display_name": title, "product_canonical_name": title, "requested_source_count": 5,
                       "source_analyses": reviews, "audience_analyses": [], "correction_issues": []}
    report = {"product_display_name": title, "product_canonical_name": title, "summary": TOPICS[0],
        "consensus_pros": [{"statement": TOPICS[0], "source_ids": [r["source_id"] for r in reviews],
                            "evidence_node_ids": evidence_ids}], "consensus_cons": [], "disagreements": [],
        "longest_usage_period": "six months", "longest_usage_source_id": reviews[0]["source_id"],
        "who_should_buy": [TOPICS[5]], "who_should_avoid": [], "limitations": ["Transcript-only evidence"]}
    consensus_spec = AGENT_REGISTRY["consensus_analyst"] if compact else LEGACY_CONSENSUS
    auditor_spec = AGENT_REGISTRY["quality_auditor"] if compact else LEGACY_AUDITOR
    _, bindings, sources = evidence_catalog(reviews)
    synthesis = {"summary": TOPICS[0], "assertions": [{"kind": "strength", "attribute": "Battery endurance",
        "observation": TOPICS[0],
        "evidence_refs": [key for key, (owner, eid) in bindings.items() if owner == source_id and eid in evidence_ids]}
        for source_ref, source_id in sources.items()],
        "longest_usage_period": "six months", "longest_usage_source_ref": next(key for key, sid in sources.items()
            if sid == reviews[0]["source_id"]), "who_should_buy": [TOPICS[5]], "limitations": ["Transcript-only evidence"]}
    if compact:
        consensus_input = repair_synthesis_input(consensus_input)
    drafted = await invoke(consensus_spec, consensus_input, "", synthesis if compact else report)
    if compact:
        drafted = drafted.as_report(title, title, reviews)
    needs_repair = case % 4 == 0
    issue = {"code": "unsupported_finding", "field_path": "report_draft.consensus_pros[0]",
             "evidence_node_ids": [], "retryable": True}
    audited = await invoke(auditor_spec, {"report_draft": report, "source_analyses": reviews}, "",
                           {"verdict": "fail" if needs_repair else "pass", "issues": [issue] if needs_repair else []})
    safe, _, terminal = ground_report(drafted, reviews, audited, strict_grounding=True)
    if needs_repair:
        assert not terminal
        repaired_input = {**consensus_input, "correction_issues": [issue]}
        if compact:
            repaired_input = repair_synthesis_input({"product_display_name": title, "product_canonical_name": title,
                "requested_source_count": 5, "source_analyses": reviews, "correction_issues": [issue],
                "report_under_repair": report})
        drafted = await invoke(consensus_spec, repaired_input, "", synthesis if compact else report)
        if compact:
            drafted = drafted.as_report(title, title, reviews)
        audited = await invoke(auditor_spec, {"report_draft": report, "source_analyses": reviews}, "",
                               {"verdict": "pass", "issues": []})
        safe, audited, terminal = ground_report(drafted, reviews, audited, strict_grounding=True)
    assert safe.consensus_pros and audited.verdict != "fail"
    topics = {claim["claim"] for review in reviews for claim in review["claims"]}
    return {"case_id": str(case), **ledger, "completion_ms": (time.perf_counter() - start) * 1000,
        "source_count_requested": 5, "source_count_analyzed": len(reviews), "quote_valid_rate": 1,
        "unsupported_claim_rate": 0, "buyer_coverage_rate": len(topics & set(TOPICS)) / len(TOPICS),
        "first_audit_repairable": needs_repair, "correction_model_calls": int(needs_repair),
        "reaudit_model_calls": int(needs_repair)}


async def benchmark(pairs=20, repetitions=3):
    await replay(99, False)
    await replay(99, True)
    baseline, candidate = {}, {}
    for case in range(pairs):
        measurements = {False: [], True: []}
        for repetition in range(repetitions):
            for compact in ((False, True) if repetition % 2 == 0 else (True, False)):
                measurements[compact].append(await replay(case, compact))
        for compact, destination in ((False, baseline), (True, candidate)):
            destination[str(case)] = {**measurements[compact][0], "completion_ms": statistics.median(
                row["completion_ms"] for row in measurements[compact])}
    return baseline, candidate, compare(baseline, candidate, min_pairs=20, strict_coverage=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".audit-cache" / "source-coverage")
    args = parser.parse_args()
    baseline, candidate, result = asyncio.run(benchmark())
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in (("baseline", baseline), ("candidate", candidate)):
        (args.output / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows.values()), encoding="utf-8")
    result["measurement"] = "offline estimates and median in-process timings; fixed 2ms local provider; no live inference"
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
