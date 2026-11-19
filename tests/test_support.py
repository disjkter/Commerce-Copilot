import asyncio
from uuid import uuid4

from commerce_copilot.contracts import AgentRole
from commerce_copilot.database import CatalogRepository
from commerce_copilot.orchestrator import handle_turn


def make_repository(tmp_path) -> CatalogRepository:
    repository = CatalogRepository(tmp_path / "commerce.db")
    repository.seed_products()
    repository.seed_support_data()
    return repository


def test_order_lookup_returns_order_items(tmp_path):
    repository = make_repository(tmp_path)

    order = repository.get_order("ORD-2026-1001")

    assert order is not None
    assert order.status == "shipped"
    assert order.items[0].product_id == "CP-AUD-101"


def test_support_seed_is_idempotent(tmp_path):
    repository = make_repository(tmp_path)

    repository.seed_support_data()
    order = repository.get_order("ORD-2026-1001")

    assert order is not None
    assert len(order.items) == 1


def test_return_policy_can_be_queried(tmp_path):
    repository = make_repository(tmp_path)

    policy = repository.get_policy("returns")

    assert policy is not None
    assert policy.title == "七日退货规则"
    assert "7日内" in policy.body


def test_order_agent_returns_grounded_order_and_policy():
    result = asyncio.run(handle_turn("订单 ORD-2026-1001 到哪了，收到后能退货吗？"))

    assert {report.role for report in result.reports} == {
        AgentRole.LOGISTICS_TRACKER,
        AgentRole.AFTER_SALES_ADVISOR,
    }
    assert "暂不符合退货资格" in result.answer
    assert "上海转运中心" in result.answer
    assert result.trace.grounding.passed is True
    assert result.trace.grounding.checked_claims >= 5


def test_after_sales_agent_returns_grounded_eligibility_decision():
    result = asyncio.run(
        handle_turn(
            "订单 ORD-2026-1003 还能退吗？",
            user_id="user-demo",
            local_only=True,
        )
    )

    assert [report.role for report in result.reports] == [
        AgentRole.AFTER_SALES_ADVISOR
    ]
    assert "符合退货资格" in result.answer
    assert "退货截止时间" in result.answer
    assert result.reports[0].evidence[0].source == "derived_return_eligibility"
    assert result.trace.grounding.passed is True


def test_order_agent_requests_id_when_missing():
    result = asyncio.run(handle_turn("帮我查询订单物流"))

    assert "请提供" in result.answer
    assert "ORD-2026-1001" in result.answer
    assert result.trace.grounding.passed is True


def test_order_agent_lists_orders_for_the_authenticated_user():
    result = asyncio.run(handle_turn("查看我的订单", user_id="user-demo"))

    assert [report.role for report in result.reports] == [AgentRole.ORDER_CARE]
    assert "ORD-2026-1001" in result.answer
    assert "ORD-2026-1002" in result.answer
    assert "ORD-2026-1003" in result.answer
    assert "请提供" not in result.answer
    assert result.trace.grounding.passed is True


def test_logistics_agent_lists_the_authenticated_users_shipments():
    result = asyncio.run(handle_turn("查看我的订单物流", user_id="user-demo"))

    assert [report.role for report in result.reports] == [
        AgentRole.LOGISTICS_TRACKER
    ]
    assert "ORD-2026-1001" in result.answer
    assert "ORD-2026-1002" in result.answer
    assert "ORD-2026-1003" in result.answer
    assert len(result.reports[0].evidence) == 3
    assert result.trace.grounding.passed is True


def test_logistics_agent_returns_grounded_tracking_timeline():
    result = asyncio.run(handle_turn("订单 ORD-2026-1001 的包裹到哪了？"))

    assert [report.role for report in result.reports] == [
        AgentRole.LOGISTICS_TRACKER
    ]
    assert "运输中" in result.answer
    assert "上海转运中心" in result.answer
    evidence = result.reports[0].evidence[0]
    assert evidence.source == "logistics_repository"
    assert evidence.reference == "shipment:SHP-2026-1001"
    assert result.trace.grounding.passed is True


def test_logistics_agent_requests_order_or_tracking_number():
    result = asyncio.run(handle_turn("帮我看看快递到哪里了"))

    assert [report.role for report in result.reports] == [
        AgentRole.LOGISTICS_TRACKER
    ]
    assert "订单号或运单号" in result.answer
    assert result.reports[0].evidence == []


def test_status_follow_up_returns_only_requested_order_field():
    conversation_id = f"support-{uuid4().hex}"
    asyncio.run(
        handle_turn("查询订单 ORD-2026-1001，并告诉我退货政策", conversation_id)
    )

    result = asyncio.run(handle_turn("它现在是什么状态？", conversation_id))

    assert "当前状态：已发货" in result.answer
    assert "下单时间" not in result.answer
    assert "商品：" not in result.answer
    assert "七日退货规则" not in result.answer
    assert result.trace.grounding.checked_claims == 1
