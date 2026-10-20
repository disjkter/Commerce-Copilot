import pytest

from commerce_copilot.comparison import compare_products
from commerce_copilot.database import DEMO_PRODUCTS
from commerce_copilot.grounding import validate_reports
from commerce_copilot.contracts import AgentReport, AgentRole


def test_comparison_derives_price_and_rating_facts():
    bundle = compare_products([DEMO_PRODUCTS[0], DEMO_PRODUCTS[1]])

    assert bundle.evidence.source == "derived_comparison"
    assert bundle.evidence.attributes["cheapest_reference"] == "CP-AUD-101"
    assert bundle.evidence.attributes["highest_rated_reference"] == "CP-AUD-102"
    assert bundle.evidence.attributes["price_gap_cents"] == 14000
    assert "相差 140.00 元" in bundle.message


def test_comparison_claims_pass_grounding():
    bundle = compare_products([DEMO_PRODUCTS[0], DEMO_PRODUCTS[1]])
    report = AgentReport(
        role=AgentRole.PRODUCT_SCOUT,
        message=bundle.message,
        evidence=[bundle.evidence],
        claims=bundle.claims,
        elapsed_ms=1,
    )

    summary = validate_reports([report])

    assert summary.passed is True
    assert summary.checked_claims == len(bundle.evidence.attributes)


def test_comparison_requires_at_least_two_products():
    with pytest.raises(ValueError, match="at least two"):
        compare_products([DEMO_PRODUCTS[0]])
