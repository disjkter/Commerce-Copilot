import asyncio

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

    assert result.reports[0].role == AgentRole.ORDER_CARE
    assert "已发货" in result.answer
    assert "七日退货规则" in result.answer
    assert result.trace.grounding.passed is True
    assert result.trace.grounding.checked_claims == 5


def test_order_agent_requests_id_when_missing():
    result = asyncio.run(handle_turn("帮我查询订单物流"))

    assert "请提供" in result.answer
    assert "ORD-2026-1001" in result.answer
    assert result.trace.grounding.passed is True
