from typing import Protocol

import httpx
from pydantic import BaseModel

from .config import Settings, get_settings
from .llm_json import parse_json_object


class LLMGatewayResponse(BaseModel):
    data: dict[str, object]
    provider: str
    model: str
    elapsed_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(Protocol):
    async def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float = 0,
        timeout_seconds: float = 15,
    ) -> LLMGatewayResponse: ...


class OpenAICompatibleClient:
    """Provider-neutral client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings or get_settings()
        self.transport = transport

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float = 0,
        timeout_seconds: float = 15,
    ) -> LLMGatewayResponse:
        from time import perf_counter

        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.settings.llm_structured_output:
            payload["response_format"] = {"type": "json_object"}
        endpoint = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        timeout = httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))
        started = perf_counter()
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return LLMGatewayResponse(
            data=parse_json_object(content),
            provider=self.settings.llm_provider,
            model=model,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
