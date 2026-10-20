import asyncio

import httpx

from commerce_copilot.config import Settings
from commerce_copilot.contracts import AgentRole
from commerce_copilot.planning import DeepSeekPlanner, local_plan


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


def test_local_plan_extracts_parallel_tasks_and_budget():
    plan = local_plan("推荐一款 500 元以内的耳机，并查询订单物流")

    assert [mission.role for mission in plan.missions] == [
        AgentRole.PRODUCT_SCOUT,
        AgentRole.ORDER_CARE,
    ]
    assert plan.missions[0].parameters["max_price_cents"] == 50000


def test_local_plan_understands_budget_without_currency_unit():
    plan = local_plan("我要一款500以下的耳机，顺便帮我看看订单物流")

    assert plan.missions[0].parameters["max_price_cents"] == 50000


def test_deepseek_json_plan_is_validated():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"tasks":[{"role":"product_scout",'
                            '"instruction":"寻找通勤耳机","product_query":"通勤 无线 耳机",'
                            '"max_price_yuan":500,"preferences":["通勤"]}]}'
                        }
                    }
                ]
            },
        )

    planner = DeepSeekPlanner(make_settings("test-key"), httpx.MockTransport(handler))
    plan = asyncio.run(planner.create_plan("推荐通勤耳机"))

    assert plan.source == "deepseek"
    assert plan.missions[0].parameters["max_price_cents"] == 50000


def test_invalid_model_output_falls_back_locally():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    planner = DeepSeekPlanner(make_settings("test-key"), httpx.MockTransport(handler))
    plan = asyncio.run(planner.create_plan("推荐一款耳机"))

    assert plan.source == "local_after_error"
    assert plan.error == "JSONDecodeError"
    assert plan.missions[0].role == AgentRole.PRODUCT_SCOUT
