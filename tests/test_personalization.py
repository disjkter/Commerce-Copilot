from datetime import datetime, timezone

from commerce_copilot.contracts import (
    AgentRole,
    MemoryItem,
    MemoryScope,
    MemorySourceType,
    MemoryType,
    Mission,
    UserPreferences,
)
from commerce_copilot.personalization import (
    needs_product_clarification,
    personalize_mission,
)


def product_mission(**parameters) -> Mission:
    return Mission(
        role=AgentRole.PRODUCT_SCOUT,
        instruction="推荐商品",
        parameters=parameters,
    )


def test_saved_preferences_fill_missing_product_constraints():
    result = personalize_mission(
        product_mission(product_query="推荐商品"),
        UserPreferences(
            max_budget_cents=50000,
            preferred_categories=["耳机"],
            scenarios=["通勤"],
        ),
    )

    assert result.parameters["max_price_cents"] == 50000
    assert result.parameters["product_query"] == "推荐商品 耳机 通勤"
    assert result.parameters["categories"] == ["耳机"]
    assert result.parameters["applied_preferences"] == {
        "max_budget_cents": 50000,
        "preferred_categories": ["耳机"],
        "scenarios": ["通勤"],
    }


def test_current_request_overrides_saved_budget_and_category():
    result = personalize_mission(
        product_mission(product_query="推荐键盘", max_price_cents=30000),
        UserPreferences(
            max_budget_cents=50000,
            preferred_categories=["耳机"],
        ),
    )

    assert result.parameters["max_price_cents"] == 30000
    assert result.parameters["product_query"] == "推荐键盘"
    assert result.parameters["categories"] == ["键盘"]
    assert result.parameters["applied_preferences"] == {}


def test_direct_product_reference_is_not_refiltered_by_saved_preferences():
    mission = product_mission(
        product_query="第一款",
        selected_references=["CP-AUD-103"],
    )

    result = personalize_mission(
        mission,
        UserPreferences(max_budget_cents=10000, priorities=["降噪"]),
    )

    assert result.parameters == mission.parameters


def test_only_generic_product_request_needs_clarification():
    assert needs_product_clarification("推荐商品") is True
    assert needs_product_clarification("推荐商品？") is True
    assert needs_product_clarification("推荐耳机") is False
    assert needs_product_clarification("推荐商品 耳机 通勤") is False


def test_active_atomic_memories_enrich_and_exclude_product_features():
    now = datetime.now(timezone.utc)
    memories = [
        MemoryItem(
            memory_id="mem-positive",
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            scope=MemoryScope.USER,
            key="preference.feature.降噪",
            value={"term": "降噪", "accepted": True},
            source_type=MemorySourceType.USER_EXPLICIT,
            confidence=1,
            valid_from=now,
            created_at=now,
        ),
        MemoryItem(
            memory_id="mem-negative",
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            scope=MemoryScope.USER,
            key="preference.feature.入耳式",
            value={"term": "入耳式", "accepted": False},
            source_type=MemorySourceType.USER_EXPLICIT,
            confidence=1,
            valid_from=now,
            created_at=now,
        ),
    ]

    result = personalize_mission(
        product_mission(product_query="推荐耳机"),
        UserPreferences(),
        memories,
    )

    assert result.parameters["product_query"] == "推荐耳机 降噪"
    assert result.parameters["excluded_terms"] == ["入耳式"]
    assert result.parameters["applied_preferences"] == {
        "memory_preferences": ["降噪"],
        "memory_exclusions": ["入耳式"],
    }
