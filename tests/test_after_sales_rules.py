from datetime import datetime, timedelta, timezone

from commerce_copilot.after_sales import (
    ReturnEligibilityDecision,
    ReturnEligibilityStatus,
)
from commerce_copilot.after_sales_rules import (
    AfterSalesRuleDecision,
    EvidenceSnapshot,
    ReturnPrecheckInput,
    evaluate_return_precheck,
)


def eligibility(status=ReturnEligibilityStatus.ELIGIBLE, reason="仍在七日退货期内"):
    now = datetime.now(timezone.utc)
    return ReturnEligibilityDecision(
        order_id="ORD-1",
        status=status,
        reason=reason,
        evaluated_at=now,
        delivered_at=now - timedelta(days=1),
        deadline_at=now + timedelta(days=6),
        return_window_days=7,
    )


def complete_evidence(**updates):
    values = {
        "submitted_types": ["product_overview", "packaging", "accessories"],
        "visual_confidence": 0.95,
    }
    values.update(updates)
    return EvidenceSnapshot(**values)


def test_missing_physical_evidence_requests_more_materials():
    result = evaluate_return_precheck(
        ReturnPrecheckInput(eligibility=eligibility(), refund_amount_cents=46_900)
    )

    assert result.decision == AfterSalesRuleDecision.REQUEST_MORE_EVIDENCE
    assert result.matched_rules == ["R-EVIDENCE-INCOMPLETE"]
    assert set(result.missing_evidence) == {
        "product_overview",
        "packaging",
        "accessories",
    }


def test_complete_low_risk_evidence_can_authorize_return_shipping():
    result = evaluate_return_precheck(
        ReturnPrecheckInput(
            eligibility=eligibility(),
            refund_amount_cents=46_900,
            evidence=complete_evidence(),
        )
    )

    assert result.decision == AfterSalesRuleDecision.AUTO_AUTHORIZE_RETURN
    assert result.risk_score == 0


def test_serial_mismatch_forces_manual_review():
    result = evaluate_return_precheck(
        ReturnPrecheckInput(
            eligibility=eligibility(),
            refund_amount_cents=46_900,
            evidence=complete_evidence(serial_number_matches=False),
        )
    )

    assert result.decision == AfterSalesRuleDecision.MANUAL_REVIEW
    assert "R-SERIAL-MISMATCH" in result.matched_rules


def test_expired_window_routes_to_manual_instead_of_automatic_rejection():
    result = evaluate_return_precheck(
        ReturnPrecheckInput(
            eligibility=eligibility(
                ReturnEligibilityStatus.INELIGIBLE,
                "已超过签收后 7 天退货期限",
            ),
            refund_amount_cents=46_900,
        )
    )

    assert result.decision == AfterSalesRuleDecision.MANUAL_REVIEW
    assert result.matched_rules == ["R-WINDOW-EXPIRED"]


def test_high_value_and_grounding_failure_never_auto_approve():
    high_value = evaluate_return_precheck(
        ReturnPrecheckInput(
            eligibility=eligibility(),
            refund_amount_cents=120_000,
            evidence=complete_evidence(),
        )
    )
    grounding_failure = evaluate_return_precheck(
        ReturnPrecheckInput(
            eligibility=eligibility(),
            refund_amount_cents=10_000,
            evidence=complete_evidence(),
            grounding_passed=False,
        )
    )

    assert high_value.decision == AfterSalesRuleDecision.MANUAL_REVIEW
    assert grounding_failure.decision == AfterSalesRuleDecision.MANUAL_REVIEW
