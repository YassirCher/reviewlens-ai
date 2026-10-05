from __future__ import annotations

import json
import os
import re
from collections import Counter

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="ReviewLens OpenRouter contract mock")
RUN_SCENARIOS: dict[str, str] = {}
ROLE_CALLS: Counter[tuple[str, str]] = Counter()
INFERENCE_MODELS: Counter[tuple[str, str]] = Counter()
AUDIENCE_MODEL = "meta-llama/llama-3.1-8b-instruct"
DEEPSEEK_FLASH_MODELS = {
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-flash-0731",
}


def _require_auth(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing authentication")


def _allowed_inference_models() -> set[str]:
    configured = os.getenv("PHASE12_ALLOWED_INFERENCE_MODELS", ",".join(sorted(DEEPSEEK_FLASH_MODELS | {AUDIENCE_MODEL})))
    return {item.strip() for item in configured.split(",") if item.strip()}


def _require_allowed_models(models: list[str]) -> None:
    allowed = _allowed_inference_models()
    if allowed and (not models or any(model not in allowed for model in models)):
        raise HTTPException(status_code=400, detail="phase12 model restriction violated")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/history")
def history() -> dict:
    return {
        "inference": [
            {"operation": operation, "model": model, "calls": calls}
            for (operation, model), calls in sorted(INFERENCE_MODELS.items())
        ]
    }


@app.post("/history/reset", status_code=204)
def reset_history() -> None:
    INFERENCE_MODELS.clear()


@app.get("/api/v1/models")
def models(authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {
        "data": [
            {
                "id": "fixture/chat-model",
                "canonical_slug": "fixture/chat-model",
                "name": "Fixture Chat",
                "description": "Local Phase 3 contract model",
                "context_length": 4096,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format", "temperature"],
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "top_provider": {"max_completion_tokens": 1024},
            },
            {
                "id": "fixture/chat-fallback",
                "canonical_slug": "fixture/chat-fallback",
                "name": "Fixture Chat Fallback",
                "description": "Local Phase 3 fallback model",
                "context_length": 4096,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format", "temperature"],
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "top_provider": {"max_completion_tokens": 1024},
            },
            {
                "id": "deepseek/deepseek-v4-flash",
                "canonical_slug": "deepseek/deepseek-v4-flash",
                "name": "DeepSeek V4 Flash Fixture",
                "description": "Local Phase 6 structured-output contract model",
                "context_length": 65536,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format", "temperature"],
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "top_provider": {"max_completion_tokens": 16384},
            },
            {
                "id": "deepseek/deepseek-v4-flash-0731",
                "canonical_slug": "deepseek/deepseek-v4-flash-0731",
                "name": "DeepSeek V4 Flash 0731 Fixture",
                "description": "Local Phase 12 pinned-model contract fixture",
                "context_length": 65536,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format", "temperature"],
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "top_provider": {"max_completion_tokens": 16384},
            },
            {
                "id": "meta-llama/llama-3.1-8b-instruct",
                "canonical_slug": "meta-llama/llama-3.1-8b-instruct",
                "name": "Llama 3.1 8B audience fixture",
                "description": "Local Phase 12 pinned-model contract fixture",
                "context_length": 65536,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format", "temperature"],
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "top_provider": {"max_completion_tokens": 16384},
            },
        ]
    }


@app.get("/api/v1/embeddings/models")
def embedding_models(authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {
        "data": [
            {
                "id": "fixture/embedding-model",
                "canonical_slug": "fixture/embedding-model",
                "name": "Fixture Embedding",
                "description": "Local Phase 3 embedding model",
                "context_length": 8192,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["embeddings"]},
                "supported_parameters": ["dimensions"],
                "pricing": {"prompt": "0.0000001"},
                "top_provider": {},
            }
        ]
    }


@app.get("/api/v1/providers")
def providers(authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {
        "data": [
            {
                "slug": "fixture",
                "name": "Fixture Provider",
                "privacy": {"data_collection": "deny"},
                "status": "available",
            }
        ]
    }


@app.get("/api/v1/models/{author}/{slug:path}/endpoints")
def endpoints(author: str, slug: str, authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    model = f"{author}/{slug}"
    if model == "fixture/refresh-failure":
        raise HTTPException(status_code=503, detail="mock endpoint refresh failure")
    return {
        "data": {
            "id": model,
            "name": model,
            "endpoints": [
                {
                    "id": f"fixture/{model}",
                    "provider_slug": "fixture",
                    "provider_name": "Fixture Provider",
                    "context_length": 65536 if model in DEEPSEEK_FLASH_MODELS | {AUDIENCE_MODEL} else 4096,
                    "max_completion_tokens": 16384 if model in DEEPSEEK_FLASH_MODELS | {AUDIENCE_MODEL} else 1024,
                    "quantization": "fp16",
                    "supported_parameters": ["response_format", "temperature"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    "privacy": {"data_collection": "deny"},
                    "status": "available",
                }
            ],
        }
    }


@app.post("/api/v1/chat/completions")
async def chat(
    request: Request,
    authorization: str | None = Header(default=None),
    x_title: str | None = Header(default=None),
    http_referer: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    if not x_title or not http_referer or not x_request_id:
        raise HTTPException(status_code=400, detail="required attribution headers missing")
    body = await request.json()
    requested_models = body.get("models") or [body.get("model")]
    requested_models = [str(model) for model in requested_models if model]
    _require_allowed_models(requested_models)
    for model in requested_models:
        INFERENCE_MODELS[("chat", model)] += 1
    mock_failure = str(body.get("metadata", {}).get("phase10_failure", ""))
    failure_responses = {
        "authentication": (401, "mock_authentication_failed"),
        "payment": (402, "mock_payment_required"),
        "rate_limit": (429, "mock_rate_limited"),
        "timeout": (408, "mock_timeout"),
        "upstream_5xx": (500, "mock_upstream_failure"),
        "provider_unavailable": (503, "mock_provider_unavailable"),
        "model_unavailable": (404, "mock_model_unavailable"),
    }
    if mock_failure in failure_responses:
        status, code = failure_responses[mock_failure]
        headers = {"Retry-After": "0"} if status == 429 else None
        raise HTTPException(status_code=status, detail={"code": code}, headers=headers)
    if body.get("provider", {}).get("require_parameters") is not True:
        raise HTTPException(status_code=400, detail="require_parameters missing")
    response_format = body.get("response_format", {})
    if response_format.get("type") not in {"json_schema", "json_object"}:
        raise HTTPException(status_code=400, detail="strict response format missing")
    schema_name = response_format.get("json_schema", {}).get("name", "")
    if response_format.get("type") == "json_object":
        if requested_models != [AUDIENCE_MODEL]:
            raise HTTPException(status_code=400, detail="audience model required")
        if body.get("provider", {}).get("only") != ["fixture"]:
            raise HTTPException(status_code=400, detail="audience must restrict requests to validated endpoints")
        schema_name = "ClassifiedAudienceDraft"
    elif AUDIENCE_MODEL in requested_models:
        raise HTTPException(status_code=400, detail="audience JSON object contract required")
    trace_id = str(body.get("metadata", {}).get("trace_id", "unknown"))
    task_input: dict = {}
    messages = body.get("messages") or []
    if messages:
        trusted_message = next((str(item.get("content", "")) for item in reversed(messages)
                                if "<trusted-task>" in str(item.get("content", ""))), "")
        match = re.search(r"<trusted-task>\s*(\{.*?\})\s*</trusted-task>", trusted_message, re.S)
        if match:
            trusted = json.loads(match.group(1))
            task_input = trusted.get("task_input", {})
            if evaluation_case := trusted.get("evaluation_case"):
                task_input["_evaluation_case_id"] = evaluation_case
    if schema_name == "ClassifiedAudienceDraft":
        comment_text = "\n".join(str(item.get("content", "")) for item in messages)
        task_input["_comment_refs"] = list(dict.fromkeys(re.findall(r"^- \[([^\]]+)\] likes=", comment_text, re.M)))
    if schema_name == "ResearchCoordinatorInput":
        raise HTTPException(status_code=400, detail="wrong schema selected")
    content = ({"phase10_invalid": True} if mock_failure == "schema_rejection"
               else _structured_content(schema_name, trace_id, task_input))
    return {
        "id": f"mock-{x_request_id}",
        "model": (body.get("models") or [body.get("model") or "deepseek/deepseek-v4-flash"])[0],
        "provider": "fixture",
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
        "service_tier": "default",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.000015,
            "prompt_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
            "completion_tokens_details": {"reasoning_tokens": 1},
        },
    }


def _scenario(product: str) -> str:
    lowered = product.casefold()
    for value in ("retry_once", "audit_uppercase_correction", "audit_correction", "audit_empty_correction", "audit_unchanged_correction", "audit_fail"):
        if value.replace("_", " ") in lowered:
            return value
    if "partial" in lowered:
        return "partial"
    if "comments" in lowered:
        return "comments"
    if "cancel" in lowered:
        return "cancel"
    return "complete"


def _structured_content(schema_name: str, trace_id: str, task_input: dict) -> dict:
    ROLE_CALLS[(trace_id, schema_name)] += 1
    if schema_name == "phase3_fixture":
        return {"ok": True}
    if schema_name == "QueryPlan":
        scenario = _scenario(str(task_input["product_name"]))
        RUN_SCENARIOS[trace_id] = scenario
        canonical = str(task_input["product_name"])
        return {
            "canonical_label": canonical,
            "aliases": [],
            "queries": [f"{canonical} long term review", f"{canonical} comparison"],
            "exclusion_hints": ["advertisement"],
            "requested_source_count": task_input["requested_source_count"],
            "requested_language": task_input["requested_language"],
            "analyze_comments": task_input["analyze_comments"],
        }
    if schema_name == "SourceCuration":
        decisions = []
        ordered = []
        for candidate in task_input["candidates"]:
            eligible = candidate.get("deterministic_exclusion") is None
            decisions.append(
                {
                    "video_id": candidate["video_id"],
                    "classification": "long_term" if eligible else "irrelevant",
                    "eligible": eligible,
                    "product_relevance": 0.95 if eligible else 0.1,
                    "review_intent": 0.95 if eligible else 0.1,
                    "independence": 0.9 if eligible else 0.1,
                    "evidence_potential": 0.9 if eligible else 0.1,
                    "reason_codes": [] if eligible else [candidate["deterministic_exclusion"]],
                }
            )
            if eligible:
                ordered.append(candidate["video_id"])
        return {"decisions": decisions, "ordered_video_ids": ordered}
    if schema_name in {"SpanVideoExtraction", "CompactSpanVideoExtraction"}:
        review = _structured_content("ClassifiedVideoExtraction", trace_id, task_input)["review"]
        for claim in review["claims"]:
            claim.pop("evidence")
            claim.update(span_refs=["c1"], confidence=90)
        review["ownership_span_refs"] = ["c1"] if task_input.get("_evaluation_case_id") == "long_term_use" else []
        return {"review": review, "product_information": {"facts": [], "variants": [], "sample_units": []}}
    if schema_name in {"VideoExtraction", "ClassifiedVideoExtraction"}:
        review = _structured_content("SourceAnalysisDraft", trace_id, task_input)
        review.pop("source_id")
        for claim in review["claims"]:
            if schema_name == "ClassifiedVideoExtraction":
                claim.update(kind="strength", topic="Battery endurance")
            for quote in claim["evidence"]:
                quote.pop("source_node_id")
        details_input = {**task_input, "canonical_product": "Aurora Headphones"} if (
            task_input.get("source_title") == "Aurora review") else task_input
        return {"review": review, "product_information": _structured_content(
            "ProductExtractionDraft", trace_id, details_input)}
    if schema_name == "SourceAnalysisDraft":
        scenario = RUN_SCENARIOS.get(trace_id, "complete")
        central = not (
            scenario == "retry_once"
            and ROLE_CALLS[(trace_id, schema_name)] == 1
        )
        source_id = task_input["source_id"]
        return {
            "source_id": source_id,
            "review_type": "long_term",
            "ownership_context": "owned",
            "usage_period_mentioned": True,
            "usage_period_raw": "six months",
            "usage_period_days_estimate": 180,
            "reviewer_sentiment_score": 78,
            "purchase_recommendation_score": 80,
            "evidence_quality_score": 84,
            "purchase_verdict": "buy_with_caveats",
            "recommendation_summary": "The tested battery endurance and value support a qualified recommendation.",
            "pros": ["battery endurance"],
            "cons": ["value depends on needs"],
            "major_issues": [],
            "recommended_for": ["long-term users"],
            "not_recommended_for": ["buyers needing a lower price"],
            "claims": [
                {
                    "claim": "The reviewer tested battery endurance and product value.",
                    "central": central,
                    "evidence": [
                        {
                            "source_node_id": source_id,
                            "evidence_text": "tested battery endurance and product value",
                            "timestamp_start_seconds": 15.0,
                            "timestamp_end_seconds": 29.0,
                            "confidence": 90,
                            "support_type": "supports",
                        }
                    ],
                }
            ],
            "limitations": ["Transcript-only analysis"],
        }
    if schema_name == "ProductExtractionDraft":
        if task_input.get("canonical_product") == "Aurora Headphones":
            return {
                "facts": [{"group": "Power", "label": "Battery runtime", "value": "30 hours",
                           "evidence": {"source_part": "description", "excerpt": "Battery lasted 30 hours."}}],
                "variants": [],
                "sample_units": [{"role": "Review unit", "details": [{
                    "label": "Color", "value": "black",
                    "evidence": {"source_part": "description", "excerpt": "Review unit is black."},
                }]}],
            }
        return {"facts": [], "variants": [], "sample_units": []}
    if schema_name == "ClassifiedAudienceDraft":
        return {"comments": [{"ref": ref, "relevant": ref != "gold-c",
                              "sentiment": "neutral" if ref in {"gold-c", "gold-e"} else "negative" if ref == "gold-d" else "positive",
                              "language": "es" if ref == "gold-e" else "en",
                              "translation": "I have Aurora Headphones, no opinion yet." if ref == "gold-e" else None}
                             for ref in task_input.get("_comment_refs", [])],
                "recurring_pros": [], "recurring_cons": [], "repeated_issues": []}
    if schema_name in {"BoundAudienceDraft", "CompactAudienceDraft"}:
        return {"positive_pct": 60, "neutral_pct": 25, "negative_pct": 15,
                "recurring_pros": [], "recurring_cons": [], "repeated_issues": [],
                "audience_agrees_with_reviewer": True, "confidence_score": 60, "sampling_limitations": []}
    if schema_name == "AudienceAnalysisDraft":
        return {
            "source_id": task_input["source_id"],
            "comments_sampled": task_input["comments_sampled"],
            "comments_retained": task_input["comments_retained"],
            "sampling_limitations": ["Top-level comments are a selected secondary sample"],
            "positive_pct": 60,
            "neutral_pct": 25,
            "negative_pct": 15,
            "recurring_pros": ["battery life"],
            "recurring_cons": ["price"],
            "repeated_issues": [],
            "audience_agrees_with_reviewer": True,
            "confidence_score": 60,
        }
    if schema_name == "GraphMutationPlan":
        analyses = task_input["source_analyses"]
        first = analyses[0]
        evidence = first["claims"][0]["evidence"][0]["evidence_node_id"]
        return {
            "findings": [
                {
                    "statement": "Independent review evidence supports battery endurance with price caveats.",
                    "source_ids": [item["source_id"] for item in analyses],
                    "evidence_node_ids": [evidence],
                    "confidence": 82,
                    "relation": ("disagreement" if task_input.get("_evaluation_case_id") ==
                                 "reviewer_disagreement" else "consensus"),
                }
            ]
        }
    if schema_name in {"AtomicBuyingSynthesis", "SourceBoundBuyingSynthesis", "EvidenceBoundBuyingSynthesis", "NormalizedBuyingSynthesis", "DistinctBuyingSynthesis", "CompleteBuyingSynthesis"}:
        if RUN_SCENARIOS.get(trace_id) == "audit_empty_correction" and task_input.get("correction_issues"):
            return {"summary": "Narrative without any cited buying findings.", "assertions": []}
        catalog = task_input["evidence_catalog"]
        refs = []
        seen = set()
        for ref in catalog:
            if ref["source_ref"] not in seen and ref["support_type"] == "supports":
                refs.append(ref["evidence_ref"])
                seen.add(ref["source_ref"])
        disagreement = task_input.get("_evaluation_case_id") == "reviewer_disagreement"
        assertions = [{"kind": "strength", "attribute": "Battery endurance",
                       "observation": "The reviewer reports tested battery endurance.",
                       "source_ref": ref["source_ref"], "evidence_refs": [ref["evidence_ref"]]}
                      for ref in catalog if ref["evidence_ref"] in refs]
        if schema_name == "AtomicBuyingSynthesis":
            assertions = [{"kind": "strength", "attribute": "Battery endurance",
                           "observation": "Reviewers report tested battery endurance.", "evidence_refs": refs}]
        if schema_name in {"EvidenceBoundBuyingSynthesis", "NormalizedBuyingSynthesis", "DistinctBuyingSynthesis", "CompleteBuyingSynthesis"}:
            for assertion in assertions:
                assertion.pop("source_ref")
                if RUN_SCENARIOS.get(trace_id) == "comments":
                    assertion["evidence_refs"] *= 2
                if task_input.get("correction_issues") and RUN_SCENARIOS.get(trace_id) != "audit_unchanged_correction":
                    assertion["observation"] = "The reviewer reports battery endurance from their test."
        return {"summary": "The cited reviews describe tested battery endurance with value caveats.",
                "assertions": assertions,
                "longest_usage_period": task_input["sources"][0]["usage_period_raw"],
                "longest_usage_source_ref": task_input["sources"][0]["source_ref"] if task_input["sources"][0]["usage_period_raw"] else None,
                "who_should_buy": ["buyers prioritizing battery endurance"],
                "who_should_avoid": ["buyers focused only on lowest price"],
                "limitations": ["YouTube transcript evidence only"],
                "disagreements": [{"topic": "long-session comfort", "side_a": "The first reviewer found the fit acceptable.",
                                   "side_a_evidence_refs": refs[:1], "side_b": "The second reviewer found the fit uncomfortable.",
                                   "side_b_evidence_refs": refs[1:2]}] if disagreement else []}
    if schema_name == "BuyingSynthesis":
        if RUN_SCENARIOS.get(trace_id) == "audit_empty_correction" and task_input.get("correction_issues"):
            return {"summary": "Narrative without any cited buying findings.", "findings": []}
        legacy = _structured_content("FinalReportDraft", trace_id, task_input)
        return {**{key: value for key, value in legacy.items() if key not in (
                    "product_display_name", "product_canonical_name", "consensus_pros", "consensus_cons")},
                "findings": [{**item, "kind": kind} for kind, field in (
                    ("strength", "consensus_pros"), ("caveat", "consensus_cons")) for item in legacy[field]]}
    if schema_name == "FinalReportDraft":
        analyses = task_input["source_analyses"]
        source_ids = [item["source_id"] for item in analyses]
        evidence_ids = [
            item["claims"][0]["evidence"][0]["evidence_node_id"] for item in analyses
        ]
        correction = bool(task_input.get("correction_issues"))
        return {
            "product_display_name": task_input["product_display_name"],
            "product_canonical_name": task_input["product_canonical_name"],
            "summary": "The available long-term review evidence supports buying with value caveats."
            + (" The identified audit issue was corrected." if correction else ""),
            "consensus_pros": [
                {
                    "statement": "Reviewers report tested battery endurance.",
                    "source_ids": source_ids,
                    "evidence_node_ids": evidence_ids,
                }
            ],
            "consensus_cons": [],
            "disagreements": ([{
                "topic": "long-session comfort",
                "side_a": "The first reviewer found the fit acceptable.",
                "side_a_source_ids": [source_ids[0]],
                "side_b": "The second reviewer found the fit uncomfortable.",
                "side_b_source_ids": [source_ids[1]],
            }] if task_input.get("_evaluation_case_id") == "reviewer_disagreement" else []),
            "longest_usage_period": "six months",
            "longest_usage_source_id": source_ids[0],
            "who_should_buy": ["buyers prioritizing battery endurance"],
            "who_should_avoid": ["buyers focused only on lowest price"],
            "limitations": ["YouTube transcript evidence only"],
        }
    if schema_name in {"OwnedAuditResult", "SupportedAuditResult"}:
        # Contract fixtures exercise gates, not live model semantic accuracy.
        scenario = RUN_SCENARIOS.get(trace_id, "complete")
        should_fail = (scenario in {"audit_fail", "audit_empty_correction", "audit_unchanged_correction"}
                       or scenario in {"audit_correction", "audit_uppercase_correction"} and ROLE_CALLS[(trace_id, schema_name)] == 1)
        response = {"decisions": {finding["field_path"]: {"supported": not should_fail,
                    "category": "material" if should_fail else None,
                    "rejected_part_ref": finding["statement"][0]["part_ref"] if should_fail else None,
                    "explanation": "Fixture-only rejection exercising repair." if should_fail else None}
                    for field in ("consensus_pros", "consensus_cons") for finding in task_input["report_draft"][field]},
                "other_issues": [{"code": "unsupported_narrative", "field_path": "report_draft.summary",
                    "rejected_part_ref": task_input["report_draft"]["summary"][0]["part_ref"],
                    "explanation": "The supplied battery quote does not support the exaggerated summary."}]
                    if task_input.get("_evaluation_case_id") else []}
        if schema_name == "SupportedAuditResult":
            findings = [*task_input.get("source_claims", []), *(f for field in ("consensus_pros", "consensus_cons") for f in task_input["report_draft"][field])]
            for index, disagreement in enumerate(task_input["report_draft"].get("disagreements", [])):
                for side in ("side_a", "side_b"):
                    findings.append({"field_path": f"report_draft.disagreements[{index}].{side}", "statement": disagreement[side], "citations": disagreement[side + "_citations"]})
            for finding in findings:
                decision = response["decisions"].setdefault(finding["field_path"], {"supported": True, "category": None, "rejected_part_ref": None, "explanation": None})
                decision["supporting_parts"] = {part["part_ref"]: [q["evidence_ref"] for q in finding["citations"]] for part in finding["statement"]} if decision["supported"] else {}
        return response
    if schema_name in {"FindingAuditResult", "ReferencedAuditResult"}:
        scenario = RUN_SCENARIOS.get(trace_id, "complete")
        should_fail = (scenario in {"audit_fail", "audit_empty_correction", "audit_unchanged_correction"}
                       or (scenario in {"audit_correction", "audit_uppercase_correction"}
                           and ROLE_CALLS[(trace_id, schema_name)] == 1))
        checks = []
        for field in ("consensus_pros", "consensus_cons"):
            for finding in task_input["report_draft"][field]:
                checks.append({"field_path": finding["field_path"], "supported": not should_fail,
                    "evidence_refs": [q["evidence_ref"] for q in finding["citations"]],
                    "category": "material" if should_fail else None,
                    ("rejected_part_ref" if schema_name == "ReferencedAuditResult" else "unsupported_clause"):
                        (finding["statement"][0]["part_ref"] if schema_name == "ReferencedAuditResult" else finding["statement"]) if should_fail else None,
                    "explanation": "Fixture-only rejection exercising the bounded correction branch." if should_fail else None})
        other = []
        if task_input.get("_evaluation_case_id"):
            other = [{"code": "unsupported_narrative", "field_path": "report_draft.summary",
                      "evidence_refs": [],
                      ("rejected_part_ref" if schema_name == "ReferencedAuditResult" else "unsupported_clause"):
                          task_input["report_draft"]["summary"][0]["part_ref"] if schema_name == "ReferencedAuditResult" else "one hundred hour battery life",
                      "explanation": "The quotation supports thirty hours, not one hundred hours or guaranteed comfort."}]
        return {"finding_checks": checks, "other_issues": other}
    if schema_name == "AuditResult":
        scenario = RUN_SCENARIOS.get(trace_id, "complete")
        audit_number = ROLE_CALLS[(trace_id, schema_name)]
        should_fail = (bool(task_input.get("_evaluation_case_id")) or scenario in {"audit_fail", "audit_empty_correction"}
                       or (scenario in {"audit_correction", "audit_uppercase_correction"} and audit_number == 1))
        if should_fail:
            paths = [f"report_draft.{field}[{index}]"
                     for field in ("consensus_pros", "consensus_cons")
                     for index, _ in enumerate(task_input.get("report_draft", {}).get(field, []))]
            if task_input.get("_evaluation_case_id"):
                paths = paths[:1]
            return {
                "verdict": "fail",
                "issues": [
                    {
                        "code": "UNSUPPORTED_FINDING" if scenario == "audit_uppercase_correction" else "unsupported_finding",
                        "field_path": path,
                        "evidence_node_ids": [],
                        "retryable": True,
                    }
                    for path in paths or ["report_draft.consensus_pros[0]"]
                ],
            }
        return {"verdict": "pass", "issues": []}
    raise HTTPException(status_code=400, detail="unknown fixture schema")


@app.post("/api/v1/embeddings")
async def embeddings(
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    body = await request.json()
    model = str(body.get("model") or "")
    _require_allowed_models([model])
    INFERENCE_MODELS[("embedding", model)] += 1
    inputs = body.get("input")
    if not isinstance(inputs, list):
        raise HTTPException(status_code=400, detail="input must be a list")
    return {
        "id": f"mock-embedding-{x_request_id}",
        "model": model,
        "provider": "fixture",
        "data": [
            {"index": index, "embedding": [1.0, float(index), 0.0]}
            for index, _ in enumerate(inputs)
        ],
        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs), "cost": 0.000001},
    }


@app.get("/api/v1/generation")
def generation(id: str, authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {
        "data": {
            "id": id,
            "model": "deepseek/deepseek-v4-flash-0731",
            "provider_name": "fixture",
            "tokens_prompt": 10,
            "tokens_completion": 5,
            "tokens": 15,
            "total_cost": 0.000015,
            "latency": 5,
        }
    }


@app.get("/api/v1/credits")
def credits(authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {"data": {"total_credits": 10, "total_usage": 1}}
