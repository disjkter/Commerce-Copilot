import asyncio

import httpx

from commerce_copilot.config import Settings
from commerce_copilot.llm_gateway import OpenAICompatibleClient


def settings(**updates) -> Settings:
    values = {
        "app_name": "test",
        "app_host": "127.0.0.1",
        "app_port": 8010,
        "database_path": "runtime/test.db",
        "llm_api_key": "secret",
        "llm_base_url": "https://provider.example/v1",
        "llm_model": "default-model",
        "llm_provider": "test-provider",
    }
    values.update(updates)
    return Settings(**values)


def test_gateway_normalizes_provider_model_json_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "planner-model"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    client = OpenAICompatibleClient(
        settings(),
        httpx.MockTransport(handler),
    )
    result = asyncio.run(
        client.generate_json(
            [{"role": "user", "content": "test"}],
            model="planner-model",
            max_tokens=100,
        )
    )

    assert result.data == {"ok": True}
    assert result.provider == "test-provider"
    assert result.model == "planner-model"
    assert result.input_tokens == 12
    assert result.output_tokens == 4


def test_gateway_can_disable_native_structured_output():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert "response_format" not in payload
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]},
        )

    client = OpenAICompatibleClient(
        settings(llm_structured_output=False),
        httpx.MockTransport(handler),
    )
    result = asyncio.run(
        client.generate_json(
            [{"role": "user", "content": "test"}],
            model="compat-model",
            max_tokens=100,
        )
    )

    assert result.data == {"ok": True}
