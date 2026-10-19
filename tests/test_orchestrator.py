import asyncio

from commerce_copilot.contracts import AgentRole
from commerce_copilot.orchestrator import handle_turn


def test_product_request_runs_product_scout_only():
    result = asyncio.run(handle_turn("推荐一款预算 500 元的通勤耳机"))

    assert [report.role for report in result.reports] == [AgentRole.PRODUCT_SCOUT]
    assert "商品发现任务" in result.answer
    assert result.reports[0].evidence
    assert all(item.attributes["price_cents"] <= 50000 for item in result.reports[0].evidence)
    assert result.elapsed_ms >= 0
    assert result.trace.planning_source == "local"


def test_mixed_request_fans_out_to_two_agents():
    result = asyncio.run(handle_turn("推荐一个键盘，另外我的订单什么时候发货？"))

    assert {report.role for report in result.reports} == {
        AgentRole.PRODUCT_SCOUT,
        AgentRole.ORDER_CARE,
    }
    assert "商品发现任务" in result.answer
    assert "购买后服务任务" in result.answer


def test_unknown_request_uses_conversation_guide():
    result = asyncio.run(handle_turn("你好"))

    assert [report.role for report in result.reports] == [AgentRole.CONVERSATION_GUIDE]
    assert "你好" in result.answer

