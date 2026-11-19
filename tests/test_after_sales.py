import asyncio
from datetime import datetime, timezone

from commerce_copilot import agents
from commerce_copilot import domain_tools
from commerce_copilot.after_sales import (
    ReturnEligibilityStatus,
    evaluate_return_eligibility,
)
from commerce_copilot.contracts import AgentRole, Mission
from commerce_copilot.database import CatalogRepository


def make_repository(tmp_path) -> CatalogRepository:
    repository = CatalogRepository(tmp_path / "after-sales.db")
    repository.seed_products()
    repository.seed_support_data()
    repository.seed_demo_identity()
    return repository


def test_unsigned_order_is_not_yet_return_eligible(tmp_path):
    repository = make_repository(tmp_path)
    order = repository.get_order("ORD-2026-1001")
    assert order is not None

    decision = evaluate_return_eligibility(
        order,
        repository.get_order_shipments("ORD-2026-1001"),
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )

    assert decision.status == ReturnEligibilityStatus.INELIGIBLE
    assert "尚未签收" in decision.reason


def test_old_delivered_order_is_outside_return_window(tmp_path):
    repository = make_repository(tmp_path)
    order = repository.get_order("ORD-2026-1002")
    assert order is not None

    decision = evaluate_return_eligibility(
        order,
        repository.get_order_shipments("ORD-2026-1002"),
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )

    assert decision.status == ReturnEligibilityStatus.INELIGIBLE
    assert "超过" in decision.reason
    assert decision.deadline_at is not None


def test_recently_delivered_order_is_return_eligible(tmp_path):
    repository = make_repository(tmp_path)
    order = repository.get_order("ORD-2026-1003")
    assert order is not None

    decision = evaluate_return_eligibility(
        order,
        repository.get_order_shipments("ORD-2026-1003"),
        now=datetime.now(timezone.utc),
    )

    assert decision.status == ReturnEligibilityStatus.ELIGIBLE
    assert decision.return_window_days == 7
    assert decision.deadline_at is not None


def test_non_returnable_item_overrides_time_window(tmp_path):
    repository = make_repository(tmp_path)
    order = repository.get_order("ORD-2026-1003")
    assert order is not None
    order.items[0].returnable = False

    decision = evaluate_return_eligibility(
        order,
        repository.get_order_shipments("ORD-2026-1003"),
    )

    assert decision.status == ReturnEligibilityStatus.INELIGIBLE
    assert "不可退商品" in decision.reason


def test_return_request_requires_confirmation_and_is_idempotent(tmp_path, monkeypatch):
    repository = make_repository(tmp_path)
    monkeypatch.setattr(agents, "get_catalog", lambda: repository)
    monkeypatch.setattr(domain_tools, "get_catalog", lambda: repository)
    draft = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction="申请退货 ORD-2026-1003",
        parameters={"user_id": "user-demo"},
    )
    confirm = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction="确认申请退货 ORD-2026-1003",
        parameters={"user_id": "user-demo"},
    )

    draft_report = asyncio.run(agents.execute_mission(draft))
    draft_message = draft_report.message
    draft_evidence = draft_report.evidence
    assert "确认申请退货" in draft_message
    assert draft_evidence[0].attributes["action_state"] == "awaiting_confirmation"
    assert repository.get_return_request("ORD-2026-1003", user_id="user-demo") is None

    created_report = asyncio.run(agents.execute_mission(confirm))
    created_message = created_report.message
    first = repository.get_return_request("ORD-2026-1003", user_id="user-demo")
    duplicate_report = asyncio.run(agents.execute_mission(confirm))
    duplicate_message = duplicate_report.message
    second = repository.get_return_request("ORD-2026-1003", user_id="user-demo")

    assert first is not None
    assert second is not None
    assert first.request_id == second.request_id
    assert first.precheck_decision == "request_more_evidence"
    assert first.rule_version == "returns-2026.11-v1"
    assert set(first.missing_evidence) == {
        "product_overview",
        "packaging",
        "accessories",
    }
    assert "已创建退货申请" in created_message
    assert "缺少材料" in created_message
    assert "未创建新工单" in duplicate_message
    assert created_report.tool_calls[0].tool_name == "returns.create_request"
    assert created_report.tool_calls[0].risk_level == "high_write"
    assert created_report.tool_calls[0].policy_decision == "allowed"


def test_ineligible_order_never_creates_return_request(tmp_path, monkeypatch):
    repository = make_repository(tmp_path)
    monkeypatch.setattr(agents, "get_catalog", lambda: repository)
    monkeypatch.setattr(domain_tools, "get_catalog", lambda: repository)
    confirm = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction="确认申请退货 ORD-2026-1001",
        parameters={"user_id": "user-demo"},
    )

    message, _, _ = asyncio.run(agents.after_sales_advisor(confirm))

    assert "暂不符合退货资格" in message
    assert repository.get_return_request("ORD-2026-1001", user_id="user-demo") is None


def test_negated_confirmation_never_creates_return_request(tmp_path, monkeypatch):
    repository = make_repository(tmp_path)
    monkeypatch.setattr(agents, "get_catalog", lambda: repository)
    monkeypatch.setattr(domain_tools, "get_catalog", lambda: repository)
    mission = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction="我不确认申请退货 ORD-2026-1003",
        parameters={"user_id": "user-demo"},
    )

    report = asyncio.run(agents.execute_mission(mission))

    assert "确认申请退货" in report.message
    assert report.tool_calls == []
    assert repository.get_return_request("ORD-2026-1003", user_id="user-demo") is None


def test_return_request_status_and_confirmed_cancellation_are_audited(
    tmp_path,
    monkeypatch,
):
    repository = make_repository(tmp_path)
    monkeypatch.setattr(agents, "get_catalog", lambda: repository)
    monkeypatch.setattr(domain_tools, "get_catalog", lambda: repository)
    created = repository.create_return_request(
        "ORD-2026-1003",
        "user-demo",
        reason="订单仍在退货期内",
    )
    assert created is not None
    request, _ = created
    assert repository.count_return_request_events(request.request_id) == 1

    status_mission = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction=f"查询退货进度 {request.request_id}",
        parameters={"user_id": "user-demo"},
    )
    cancel_mission = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction=f"取消退货申请 {request.request_id}",
        parameters={"user_id": "user-demo"},
    )
    confirm_cancel_mission = Mission(
        role=AgentRole.AFTER_SALES_ADVISOR,
        instruction=f"确认取消退货申请 {request.request_id}",
        parameters={"user_id": "user-demo"},
    )

    status_message = asyncio.run(agents.execute_mission(status_mission)).message
    cancel_message = asyncio.run(agents.execute_mission(cancel_mission)).message
    still_pending = repository.get_return_request_by_id(
        request.request_id,
        user_id="user-demo",
    )
    confirmed_report = asyncio.run(agents.execute_mission(confirm_cancel_mission))
    confirmed_message = confirmed_report.message
    cancelled = repository.get_return_request_by_id(
        request.request_id,
        user_id="user-demo",
    )
    repeated_message = asyncio.run(
        agents.execute_mission(confirm_cancel_mission)
    ).message

    assert "待审核" in status_message
    assert "确认取消退货申请" in cancel_message
    assert still_pending is not None and still_pending.status == "pending_review"
    assert "已取消" in confirmed_message
    assert cancelled is not None and cancelled.status == "cancelled"
    assert "无需重复" in repeated_message
    assert repository.count_return_request_events(request.request_id) == 2
    assert confirmed_report.tool_calls[0].tool_name == "returns.cancel_request"
    assert confirmed_report.tool_calls[0].policy_decision == "allowed"
