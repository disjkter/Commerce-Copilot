import asyncio

import httpx

from commerce_copilot.config import Settings
from commerce_copilot.contracts import AgentRole, ConversationTurn, Evidence
from commerce_copilot.planning import DeepSeekPlanner, local_plan
from commerce_copilot.query_understanding import ResponseFocus


UNDERSTANDING_JSON = (
    '"understanding":{"intents":["product"],"focus":"summary",'
    '"constraints":{"max_price_cents":50000,"preferences":["通勤"],'
    '"excluded_terms":[]},"referenced_products":[],"confidence":0.95,'
    '"needs_clarification":false,"clarification_question":null},'
)


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


def test_local_plan_resolves_order_reference_from_history():
    history = [
        ConversationTurn(
            user_message="查询订单 ORD-2026-1001",
            assistant_answer="订单已经发货。",
        )
    ]

    plan = local_plan("它现在是什么状态？", history)

    assert plan.missions[0].role == AgentRole.ORDER_CARE
    assert "ORD-2026-1001" in plan.missions[0].instruction


def test_local_plan_keeps_product_budget_for_cheaper_follow_up():
    history = [
        ConversationTurn(
            user_message="推荐500元以下的通勤耳机",
            assistant_answer="找到了两款耳机。",
        )
    ]

    plan = local_plan("再便宜一点", history)

    assert plan.missions[0].role == AgentRole.PRODUCT_SCOUT
    assert plan.missions[0].parameters["max_price_cents"] == 50000
    assert plan.missions[0].parameters["sort_by"] == "price_asc"


def test_local_plan_resolves_second_product_from_ordered_evidence():
    history = [
        ConversationTurn(
            user_message="推荐通勤耳机",
            assistant_answer="找到两款商品",
            evidence=[
                Evidence(source="local_catalog", reference="CP-AUD-102"),
                Evidence(source="local_catalog", reference="CP-AUD-101"),
            ],
        )
    ]

    plan = local_plan("第二款怎么样？", history)

    assert plan.missions[0].role == AgentRole.PRODUCT_SCOUT
    assert plan.missions[0].parameters["selected_references"] == ["CP-AUD-101"]


def test_local_understanding_recognizes_natural_focus_and_typo():
    advantages = local_plan("第一款最值得买的地方是什么？", [
        ConversationTurn(
            user_message="推荐键盘",
            assistant_answer="找到商品",
            evidence=[Evidence(source="local_catalog", reference="CP-KEY-201")],
        )
    ])
    stock = local_plan("这款还有库寸吗？", [
        ConversationTurn(user_message="推荐耳机", assistant_answer="找到商品")
    ])

    assert advantages.understanding.focus == ResponseFocus.ADVANTAGES
    assert advantages.missions[0].parameters["response_focus"] == "advantages"
    assert stock.understanding.focus == ResponseFocus.STOCK


def test_local_plan_accepts_previous_budget_near_miss_without_old_budget():
    history = [
        ConversationTurn(
            user_message="我要耳机，预算不超过300元",
            assistant_answer="候补商品价格329元，超出预算29元。",
            evidence=[
                Evidence(
                    source="local_catalog",
                    reference="CP-AUD-101",
                    attributes={"match_status": "near_miss"},
                )
            ],
        )
    ]

    plan = local_plan("可以，提高预算", history)

    assert plan.missions[0].role == AgentRole.PRODUCT_SCOUT
    assert plan.missions[0].parameters["selected_references"] == ["CP-AUD-101"]
    assert "max_price_cents" not in plan.missions[0].parameters
    assert plan.missions[0].parameters["relax_budget"] is True


def test_deepseek_json_plan_is_validated():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{' + UNDERSTANDING_JSON + '"tasks":[{"role":"product_scout",'
                            '"instruction":"寻找通勤耳机","product_query":"通勤 无线 耳机",'
                            '"max_price_yuan":500,"preferences":["通勤"],'
                            '"selected_product_references":["INVENTED-ID"]}]}'
                        }
                    }
                ]
            },
        )

    planner = DeepSeekPlanner(make_settings("test-key"), httpx.MockTransport(handler))
    plan = asyncio.run(planner.create_plan("推荐通勤耳机"))

    assert plan.source == "deepseek"
    assert plan.missions[0].parameters["max_price_cents"] == 50000
    assert "selected_references" not in plan.missions[0].parameters


def test_deepseek_plan_can_accept_grounded_near_miss():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{' + UNDERSTANDING_JSON + '"tasks":[{"role":"product_scout",'
                            '"instruction":"接受候补商品","product_query":"耳机",'
                            '"selected_product_references":["CP-AUD-101"],'
                            '"relax_budget":true}]}'
                        }
                    }
                ]
            },
        )

    history = [
        ConversationTurn(
            user_message="我要300元以内的耳机",
            assistant_answer="候补商品超出预算29元。",
            evidence=[
                Evidence(
                    source="local_catalog",
                    reference="CP-AUD-101",
                    attributes={"match_status": "near_miss"},
                )
            ],
        )
    ]
    planner = DeepSeekPlanner(make_settings("test-key"), httpx.MockTransport(handler))

    plan = asyncio.run(planner.create_plan("可以，提高预算", history))

    assert plan.missions[0].parameters["selected_references"] == ["CP-AUD-101"]
    assert plan.missions[0].parameters["relax_budget"] is True


def test_invalid_model_output_falls_back_locally():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    planner = DeepSeekPlanner(make_settings("test-key"), httpx.MockTransport(handler))
    plan = asyncio.run(planner.create_plan("推荐一款耳机"))

    assert plan.source == "local_after_error"
    assert plan.error == "JSONDecodeError"
    assert plan.missions[0].role == AgentRole.PRODUCT_SCOUT


def test_low_confidence_model_plan_requests_clarification():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"understanding":{"intents":["product"],'
                            '"focus":"summary","constraints":{},'
                            '"referenced_products":[],"confidence":0.4,'
                            '"needs_clarification":true,'
                            '"clarification_question":"你想购买哪一类商品？"},'
                            '"tasks":[{"role":"product_scout",'
                            '"instruction":"推荐商品","product_query":"商品"}]}'
                        }
                    }
                ]
            },
        )

    planner = DeepSeekPlanner(make_settings("test-key"), httpx.MockTransport(handler))
    plan = asyncio.run(planner.create_plan("帮我选一个"))

    assert plan.missions[0].role == AgentRole.CONVERSATION_GUIDE
    assert plan.missions[0].parameters["clarification_question"] == "你想购买哪一类商品？"
