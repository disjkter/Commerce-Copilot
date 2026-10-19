import asyncio

import httpx

from commerce_copilot.answering import DeepSeekAnswerComposer
from commerce_copilot.config import Settings
from commerce_copilot.contracts import AgentReport, AgentRole, Evidence


def make_settings(api_key: str) -> Settings:
    return Settings(
        app_name="test",
        app_host="127.0.0.1",
        app_port=8010,
        database_path="runtime/test.db",
        llm_api_key=api_key,
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-flash",
    )


def make_report() -> AgentReport:
    return AgentReport(
        role=AgentRole.PRODUCT_SCOUT,
        message="云雀 Lite 售价 329 元。",
        evidence=[
            Evidence(
                source="local_catalog",
                reference="CP-AUD-101",
                attributes={"title": "云雀 Lite", "price_cents": 32900},
            )
        ],
        elapsed_ms=1,
    )


def test_composer_uses_local_answer_without_api_key():
    composer = DeepSeekAnswerComposer(make_settings(""))

    result = asyncio.run(composer.create_answer("推荐耳机", [make_report()], "本地回答"))

    assert result.answer == "本地回答"
    assert result.source == "local"


def test_grounded_model_answer_is_accepted():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"answer":"云雀 Lite 售价 329 元 [CP-AUD-101]。",'
                            '"claims":[{"statement":"售价为329元",'
                            '"evidence_reference":"CP-AUD-101",'
                            '"attribute":"price_cents","expected_value":32900}]}'
                        }
                    }
                ]
            },
        )

    composer = DeepSeekAnswerComposer(
        make_settings("test-key"),
        httpx.MockTransport(handler),
    )
    result = asyncio.run(composer.create_answer("推荐耳机", [make_report()], "本地回答"))

    assert result.source == "deepseek"
    assert "[CP-AUD-101]" in result.answer


def test_changed_evidence_value_falls_back_locally():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"answer":"云雀 Lite 售价 299 元 [CP-AUD-101]。",'
                            '"claims":[{"statement":"售价为299元",'
                            '"evidence_reference":"CP-AUD-101",'
                            '"attribute":"price_cents","expected_value":29900}]}'
                        }
                    }
                ]
            },
        )

    composer = DeepSeekAnswerComposer(
        make_settings("test-key"),
        httpx.MockTransport(handler),
    )
    result = asyncio.run(composer.create_answer("推荐耳机", [make_report()], "本地回答"))

    assert result.answer == "本地回答"
    assert result.source == "local_after_error"
    assert result.error == "GeneratedAnswerGroundingError"
