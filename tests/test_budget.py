import pytest

from commerce_copilot.budget import extract_budget_cents


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("我要一款500以下的耳机", 50000),
        ("推荐 500 元以内的耳机", 50000),
        ("价格不超过￥399.5", 39950),
        ("低于 300 块钱", 30000),
        ("预算为 699 元", 69900),
        ("最多 ¥ 1200", 120000),
    ),
)
def test_extract_budget_variants(text, expected):
    assert extract_budget_cents(text) == expected


def test_text_without_maximum_budget_returns_none():
    assert extract_budget_cents("推荐一款无线耳机") is None
