from commerce_copilot.contracts import (
    AgentReport,
    AgentRole,
    Claim,
    Evidence,
)
from commerce_copilot.grounding import validate_reports
from commerce_copilot.orchestrator import compose


def make_report(*, expected_price: int = 32900, include_price: bool = True) -> AgentReport:
    attributes = {"title": "云雀 Lite 通勤蓝牙耳机"}
    if include_price:
        attributes["price_cents"] = 32900
    return AgentReport(
        role=AgentRole.PRODUCT_SCOUT,
        message="云雀 Lite 通勤蓝牙耳机价格为329元。",
        evidence=[
            Evidence(
                source="local_catalog",
                reference="CP-AUD-101",
                attributes=attributes,
            )
        ],
        claims=[
            Claim(
                statement="CP-AUD-101价格为329元",
                evidence_reference="CP-AUD-101",
                attribute="price_cents",
                expected_value=expected_price,
            )
        ],
        elapsed_ms=1.0,
    )


def test_grounding_accepts_claim_supported_by_evidence():
    summary = validate_reports([make_report()])

    assert summary.passed is True
    assert summary.checked_claims == 1
    assert summary.issues == []


def test_grounding_rejects_value_mismatch():
    summary = validate_reports([make_report(expected_price=39900)])

    assert summary.passed is False
    assert summary.issues[0].reason == "value_mismatch"


def test_grounding_rejects_missing_attribute():
    summary = validate_reports([make_report(include_price=False)])

    assert summary.passed is False
    assert summary.issues[0].reason == "missing_attribute"


def test_composer_hides_report_that_fails_grounding():
    result = compose({"reports": [make_report(expected_price=39900)]})

    assert result["grounding"].passed is False
    assert "329元" not in result["answer"]
    assert "未通过事实校验" in result["answer"]
