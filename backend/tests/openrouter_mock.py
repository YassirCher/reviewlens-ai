from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="ReviewLens OpenRouter contract mock")


def _require_auth(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing authentication")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
    return {
        "data": {
            "id": model,
            "name": model,
            "endpoints": [
                {
                    "id": f"fixture/{model}",
                    "provider_slug": "fixture",
                    "provider_name": "Fixture Provider",
                    "context_length": 4096,
                    "max_completion_tokens": 1024,
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
    if body.get("provider", {}).get("require_parameters") is not True:
        raise HTTPException(status_code=400, detail="require_parameters missing")
    response_format = body.get("response_format", {})
    if response_format.get("type") != "json_schema":
        raise HTTPException(status_code=400, detail="strict response format missing")
    return {
        "id": f"mock-{x_request_id}",
        "model": "fixture/chat-fallback",
        "provider": "fixture",
        "choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\":true}"}}],
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


@app.post("/api/v1/embeddings")
async def embeddings(
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    body = await request.json()
    inputs = body.get("input")
    if not isinstance(inputs, list):
        raise HTTPException(status_code=400, detail="input must be a list")
    return {
        "id": f"mock-embedding-{x_request_id}",
        "model": body.get("model"),
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
