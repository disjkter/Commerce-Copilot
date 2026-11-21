from commerce_copilot.contracts import (
    AgentRole,
    ConversationMemory,
    ConversationTurn,
    Evidence,
)
from commerce_copilot.memory import extract_memory_candidates, merge_conversation_memory
from commerce_copilot.planning import local_plan


def test_memory_extracts_entities_budget_and_latest_product_evidence():
    products = [
        Evidence(
            source="local_catalog",
            reference="CP-AUD-101",
            attributes={"title": "云雀 Lite 通勤蓝牙耳机"},
        ),
        Evidence(
            source="local_catalog",
            reference="CP-AUD-102",
            attributes={"title": "潮汐 Buds 2 降噪耳机"},
        ),
    ]
    memory = merge_conversation_memory(
        None,
        [
            (
                1,
                ConversationTurn(
                    user_message="预算500元，推荐两款耳机",
                    assistant_answer="找到两款商品",
                    evidence=products,
                ),
            ),
            (
                2,
                ConversationTurn(
                    user_message="查询订单 ORD-2026-1001 的物流",
                    assistant_answer="订单正在运输中",
                ),
            ),
        ],
    )

    assert memory.constraints["max_price_cents"] == 50000
    assert memory.entities["order_ids"] == ["ORD-2026-1001"]
    assert memory.entities["product_ids"] == ["CP-AUD-101", "CP-AUD-102"]
    assert [item.reference for item in memory.evidence] == [
        "CP-AUD-101",
        "CP-AUD-102",
    ]
    assert memory.source_turn_count == 2


def test_product_reference_can_use_compacted_memory_after_irrelevant_turns():
    memory = ConversationMemory(
        summary="涉及主题：product；最近商品：CP-AUD-101、CP-AUD-102",
        entities={"topics": ["product"], "product_ids": ["CP-AUD-101", "CP-AUD-102"]},
        evidence=[
            Evidence(source="local_catalog", reference="CP-AUD-101"),
            Evidence(source="local_catalog", reference="CP-AUD-102"),
        ],
        compacted_through_turn_id=3,
        source_turn_count=3,
    )
    history = [
        memory.as_context_turn(),
        ConversationTurn(user_message="你好", assistant_answer="你好"),
        ConversationTurn(user_message="谢谢", assistant_answer="不客气"),
    ]

    plan = local_plan("第二款怎么样？", history)

    assert [mission.role for mission in plan.missions] == [AgentRole.PRODUCT_SCOUT]
    assert plan.missions[0].parameters["selected_references"] == ["CP-AUD-102"]


def test_order_reference_can_use_compacted_memory_after_irrelevant_turns():
    memory = ConversationMemory(
        summary="涉及主题：order；相关订单：ORD-2026-1001",
        entities={"topics": ["order"], "order_ids": ["ORD-2026-1001"]},
        compacted_through_turn_id=2,
        source_turn_count=2,
    )
    history = [
        memory.as_context_turn(),
        ConversationTurn(user_message="你好", assistant_answer="你好"),
        ConversationTurn(user_message="谢谢", assistant_answer="不客气"),
    ]

    plan = local_plan("它现在是什么状态？", history)

    assert [mission.role for mission in plan.missions] == [AgentRole.ORDER_CARE]
    assert "ORD-2026-1001" in plan.missions[0].instruction


def test_atomic_memory_extraction_keeps_provenance_and_scope():
    candidates = extract_memory_candidates(
        "我不喜欢入耳式，这次预算不超过600元，查订单 ORD-2026-1001",
        [Evidence(source="local_catalog", reference="CP-AUD-101")],
    )
    by_key = {candidate.key: candidate for candidate in candidates}

    assert by_key["constraint.max_price_cents"].value == {
        "max_price_cents": 60000
    }
    assert by_key["entity.recent_order_ids"].value == {
        "order_ids": ["ORD-2026-1001"]
    }
    assert by_key["entity.recent_product_ids"].evidence_reference == "CP-AUD-101"
    preference = by_key["preference.feature.入耳式"]
    assert preference.value == {"term": "入耳式", "accepted": False}
    assert preference.scope.value == "conversation"
    assert preference.source_type.value == "user_explicit"


def test_explicit_preference_defaults_to_cross_conversation_user_scope():
    candidates = extract_memory_candidates("我更喜欢头戴式耳机")

    assert len(candidates) == 1
    assert candidates[0].key == "preference.feature.头戴式"
    assert candidates[0].scope.value == "user"


def test_mixed_positive_and_negative_preferences_are_extracted_per_clause():
    candidates = extract_memory_candidates("我不喜欢入耳式，但更喜欢头戴式")
    by_key = {candidate.key: candidate for candidate in candidates}

    assert by_key["preference.feature.入耳式"].value["accepted"] is False
    assert by_key["preference.feature.头戴式"].value["accepted"] is True
