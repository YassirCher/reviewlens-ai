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


def _require_auth(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing authentication")


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
                "top_provider": {"max_completion_tokens": 8192},
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
                    "context_length": 65536 if model == "deepseek/deepseek-v4-flash" else 4096,
                    "max_completion_tokens": 8192 if model == "deepseek/deepseek-v4-flash" else 1024,
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
    allowed_model = os.getenv("PHASE11_ALLOWED_INFERENCE_MODEL", "")
    if allowed_model and requested_models != [allowed_model]:
        raise HTTPException(status_code=400, detail="phase11 model restriction violated")
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
    if response_format.get("type") != "json_schema":
        raise HTTPException(status_code=400, detail="strict response format missing")
    schema_name = response_format.get("json_schema", {}).get("name", "")
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
    if schema_name == "ResearchCoordinatorInput":
        raise HTTPException(status_code=400, detail="wrong schema selected")
    content = ({"phase10_invalid": True} if mock_failure == "schema_rejection"
               else _structured_content(schema_name, trace_id, task_input))
    return {
        "id": f"mock-{x_request_id}",
        "model": (body.get("models") or [body.get("model") or "fixture/chat-fallback"])[0],
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
    for value in ("retry_once", "audit_correction", "audit_fail"):
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
    if schema_name == "AuditResult":
        scenario = RUN_SCENARIOS.get(trace_id, "complete")
        audit_number = ROLE_CALLS[(trace_id, schema_name)]
        should_fail = (bool(task_input.get("_evaluation_case_id")) or scenario == "audit_fail"
                       or (scenario == "audit_correction" and audit_number == 1))
        if should_fail:
            return {
                "verdict": "fail",
                "issues": [
                    {
                        "code": "summary_scope_requires_correction",
                        "field_path": "summary",
                        "evidence_node_ids": [],
                        "retryable": True,
                    }
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
    allowed_model = os.getenv("PHASE11_ALLOWED_INFERENCE_MODEL", "")
    if allowed_model and model != allowed_model:
        raise HTTPException(status_code=400, detail="phase11 model restriction violated")
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
            "model": "fixture/chat-fallback",
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
