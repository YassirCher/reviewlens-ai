from app.config import settings
from app.providers.base import AIProvider, ProviderError
from app.providers.openai_compatible import OpenAICompatibleProvider


def get_provider(name: str) -> AIProvider:
    if name == "openrouter":
        return OpenAICompatibleProvider(
            name="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
            extra_headers={
                "HTTP-Referer": settings.openrouter_app_url,
                "X-OpenRouter-Title": settings.openrouter_app_name,
            },
        )

    if name == "xai":
        return OpenAICompatibleProvider(
            name="xAI",
            base_url="https://api.x.ai/v1",
            api_key=settings.xai_api_key,
        )

    if name == "openai":
        return OpenAICompatibleProvider(
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            api_key=settings.openai_api_key,
        )

    raise ProviderError(f"Unsupported AI provider: {name}")
