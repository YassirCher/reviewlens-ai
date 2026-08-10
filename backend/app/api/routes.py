import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models import AnalyzeRequest, AnalyzeResponse, ConfigResponse, ProviderInfo
from app.services.orchestrator import AnalysisOrchestrator

router = APIRouter()
orchestrator = AnalysisOrchestrator()


@router.get("/config", response_model=ConfigResponse, tags=["system"])
async def config() -> ConfigResponse:
    providers = [
        ProviderInfo(
            id="openrouter",
            label="OpenRouter",
            available=settings.provider_available("openrouter"),
            model=settings.openrouter_model,
            free_friendly=settings.openrouter_model == "openrouter/free" or settings.openrouter_model.endswith(":free"),
        ),
        ProviderInfo(
            id="xai",
            label="Grok / xAI",
            available=settings.provider_available("xai"),
            model=settings.xai_model,
        ),
        ProviderInfo(
            id="openai",
            label="OpenAI",
            available=settings.provider_available("openai"),
            model=settings.openai_model,
        ),
    ]
    return ConfigResponse(
        providers=providers,
        default_provider=settings.ai_primary_provider,
    )


def _validated_name(request: AnalyzeRequest) -> str:
    name = " ".join(request.product_name.split()).strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Product name is required.")
    return name


@router.post("/analyze", response_model=AnalyzeResponse, tags=["analysis"])
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    return await orchestrator.run(
        product_name=_validated_name(request),
        analyze_comments=request.analyze_comments,
        provider_choice=request.provider,
    )


@router.post("/analyze/stream", tags=["analysis"])
async def analyze_stream(request: AnalyzeRequest) -> StreamingResponse:
    product_name = _validated_name(request)

    async def event_generator():
        try:
            async for event in orchestrator.run_stream(
                product_name=product_name,
                analyze_comments=request.analyze_comments,
                provider_choice=request.provider,
            ):
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
