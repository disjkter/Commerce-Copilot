from commerce_copilot.contracts import AgentRole, Mission, UserPreferences
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
