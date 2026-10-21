import asyncio
from uuid import uuid4

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
    assert result.trace.answer_source == "local"
    assert result.trace.grounding.passed is True
    assert result.trace.grounding.checked_claims > 0


def test_generic_product_request_asks_for_missing_requirements():
    result = asyncio.run(handle_turn("推荐商品"))

    assert [report.role for report in result.reports] == [AgentRole.PRODUCT_SCOUT]
    assert result.reports[0].evidence == []
    assert "哪一类商品" in result.answer
    assert "预算" in result.answer


def test_mixed_request_fans_out_to_two_agents():
    result = asyncio.run(handle_turn("推荐一个键盘，另外我的订单什么时候发货？"))

    assert {report.role for report in result.reports} == {
        AgentRole.PRODUCT_SCOUT,
        AgentRole.ORDER_CARE,
    }
    assert "商品发现任务" in result.answer
    assert "发货与物流规则" in result.answer
    assert "请提供" in result.answer


def test_budget_without_yuan_unit_filters_product_results():
    result = asyncio.run(handle_turn("我要一款500以下的耳机，顺便帮我看看订单物流"))
    product_report = next(
        report for report in result.reports if report.role == AgentRole.PRODUCT_SCOUT
    )

    assert product_report.evidence
    assert all(
        item.attributes["price_cents"] <= 50000
        for item in product_report.evidence
    )
    assert all(item.reference != "CP-AUD-103" for item in product_report.evidence)


def test_explicit_category_never_returns_a_different_product_type():
    result = asyncio.run(handle_turn("我要耳机，预算不超过300元，办公用"))
    product_report = next(
        report for report in result.reports if report.role == AgentRole.PRODUCT_SCOUT
    )

    assert len(product_report.evidence) == 1
    near_miss = product_report.evidence[0]
    assert near_miss.reference == "CP-AUD-101"
    assert near_miss.attributes["match_status"] == "near_miss"
    assert near_miss.attributes["budget_gap_cents"] == 2900
    assert "超出预算 29.00 元" in result.answer
    assert "鼠标" not in result.answer
    assert result.trace.grounding.passed is True


def test_user_can_accept_budget_near_miss_in_follow_up():
    conversation_id = f"near-miss-{uuid4().hex}"
    first = asyncio.run(
        handle_turn("我要耳机，预算不超过300元，办公用", conversation_id)
    )
    assert first.reports[0].evidence[0].attributes["match_status"] == "near_miss"

    accepted = asyncio.run(handle_turn("可以，提高预算", conversation_id))

    assert accepted.reports[0].evidence[0].reference == "CP-AUD-101"
    assert "329.00 元" in accepted.answer
    assert accepted.trace.grounding.passed is True


def test_follow_up_uses_persisted_conversation_context():
    conversation_id = f"test-{uuid4().hex}"
    asyncio.run(handle_turn("查询订单 ORD-2026-1001", conversation_id))

    result = asyncio.run(handle_turn("它现在是什么状态？", conversation_id))

    assert result.conversation_id == conversation_id
    assert [report.role for report in result.reports] == [AgentRole.ORDER_CARE]
    assert "ORD-2026-1001" in result.answer
    assert "已发货" in result.answer


def test_product_ordinal_uses_previous_evidence_order():
    conversation_id = f"product-{uuid4().hex}"
    first = asyncio.run(
        handle_turn("推荐500元以下的通勤耳机", conversation_id)
    )
    first_products = next(
        report.evidence
        for report in first.reports
        if report.role == AgentRole.PRODUCT_SCOUT
    )

    second = asyncio.run(handle_turn("第二款怎么样？", conversation_id))
    selected_products = next(
        report.evidence
        for report in second.reports
        if report.role == AgentRole.PRODUCT_SCOUT
    )

    assert len(selected_products) == 1
    assert selected_products[0].reference == first_products[1].reference
    assert selected_products[0].attributes["retrieval_sources"] == [
        "conversation_memory"
    ]

    comparison = asyncio.run(
        handle_turn("第一款和第二款对比一下", conversation_id)
    )
    compared_products = next(
        [item for item in report.evidence if item.source == "local_catalog"]
        for report in comparison.reports
        if report.role == AgentRole.PRODUCT_SCOUT
    )

    assert [item.reference for item in compared_products] == [
        first_products[0].reference,
        first_products[1].reference,
    ]
    comparison_evidence = next(
        item
        for report in comparison.reports
        for item in report.evidence
        if item.source == "derived_comparison"
    )
    assert comparison_evidence.attributes["price_gap_cents"] == 14000
    assert "相差 140.00 元" in comparison.answer


def test_unknown_request_uses_conversation_guide():
    result = asyncio.run(handle_turn("你好"))

    assert [report.role for report in result.reports] == [AgentRole.CONVERSATION_GUIDE]
    assert "你好" in result.answer

