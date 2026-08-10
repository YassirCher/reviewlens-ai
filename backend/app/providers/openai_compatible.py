import asyncio
import json
from copy import deepcopy
from typing import Any

import httpx

from app.config import settings
from app.providers.base import AIProvider, ProviderError


def _strictify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make Pydantic JSON schemas friendlier to strict structured-output APIs."""
    result = deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                props = node.get("properties", {})
                if props:
                    node["required"] = list(props.keys())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(result)
    return result


def _message_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("AI provider returned an unexpected response shape.") from exc

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
        return "".join(pieces)
    raise ProviderError("AI provider did not return text content.")


def _clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class OpenAICompatibleProvider(AIProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra_headers = extra_headers or {}

    async def structured_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        schema_name: str,
        model: str,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError(f"{self.name} API key is not configured.")
        if not model:
            raise ProviderError(f"No model configured for {self.name}.")

        strict_schema = _strictify_schema(schema)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        core_payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.ai_temperature,
            "max_tokens": settings.ai_max_output_tokens,
        }
        structured_payload = {
            **core_payload,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": strict_schema,
                },
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
            response = await self._post_with_single_retry(client, headers, structured_payload)

            # Some free/legacy models may not support json_schema even though the provider does.
            # Fall back once to json_object and validate locally with Pydantic.
            if response.status_code in {400, 404, 422}:
                fallback_prompt = (
                    user_prompt
                    + "\n\nReturn only one JSON object. It MUST match this JSON Schema:\n"
                    + json.dumps(strict_schema, ensure_ascii=False, separators=(",", ":"))
                )
                fallback_payload = {
                    **core_payload,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": fallback_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                }
                response = await self._post_with_single_retry(client, headers, fallback_payload)

        if response.status_code >= 400:
            detail = response.text[:1000]
            raise ProviderError(
                f"{self.name} request failed ({response.status_code}): {detail}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
            raw = _clean_json_text(_message_content(payload))
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON.") from exc

    async def _post_with_single_retry(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 500:
            await asyncio.sleep(0.6)
            response = await client.post(url, headers=headers, json=payload)
        return response
