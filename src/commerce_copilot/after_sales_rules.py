from enum import StrEnum

from pydantic import BaseModel, Field

from .after_sales import ReturnEligibilityDecision, ReturnEligibilityStatus


RULE_VERSION = "returns-2026.11-v1"
AUTO_REVIEW_AMOUNT_CENTS = 50_000
HARD_REVIEW_AMOUNT_CENTS = 100_000
GENERAL_EVIDENCE = {"product_overview", "packaging", "accessories"}
ACTIVATED_DEVICE_EVIDENCE = {"serial_number", "activation_status", "device_surface"}
ACTIVATED_DEVICE_CATEGORIES = {"手机", "平板", "电脑"}
EVIDENCE_TYPE_LABELS = {
    "product_overview": "商品整体照片",
    "packaging": "商品包装照片",
    "accessories": "配件完整照片",
    "serial_number": "序列号照片",
    "activation_status": "设备激活状态",
    "device_surface": "设备外观照片",
}


class AfterSalesStage(StrEnum):
    APPLICATION_ACCEPTANCE = "application_acceptance"
    RETURN_PRECHECK = "return_precheck"
    REFUND_INSPECTION = "refund_inspection"


class AfterSalesRuleDecision(StrEnum):
    REJECT = "reject"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    MANUAL_REVIEW = "manual_review"
    AUTO_AUTHORIZE_RETURN = "auto_authorize_return"


class EvidenceSnapshot(BaseModel):
    submitted_types: list[str] = Field(default_factory=list)
    serial_number_matches: bool | None = None
    visual_confidence: float | None = Field(default=None, ge=0, le=1)
    conflicts: list[str] = Field(default_factory=list)


class ReturnPrecheckInput(BaseModel):
    eligibility: ReturnEligibilityDecision
    refund_amount_cents: int = Field(ge=0)
    product_categories: list[str] = Field(default_factory=list)
    product_tags: list[str] = Field(default_factory=list)
    evidence: EvidenceSnapshot = Field(default_factory=EvidenceSnapshot)
    recent_return_count: int = Field(default=0, ge=0)
    grounding_passed: bool = True


class AfterSalesRuleResult(BaseModel):
    decision: AfterSalesRuleDecision
    stage: AfterSalesStage = AfterSalesStage.RETURN_PRECHECK
    risk_score: int = Field(ge=0)
    risk_level: str
    matched_rules: list[str]
    reasons: list[str]
    missing_evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    rule_version: str = RULE_VERSION


def evaluate_return_precheck(context: ReturnPrecheckInput) -> AfterSalesRuleResult:
    """Run deterministic, versioned rules for return-shipping authorization."""
    eligibility = context.eligibility
    if not context.grounding_passed:
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            100,
            ["R-GROUNDING-FAILED"],
            ["关键事实未通过证据校验"],
        )
    if eligibility.status == ReturnEligibilityStatus.NEEDS_REVIEW:
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            70,
            ["R-ELIGIBILITY-UNCERTAIN"],
            [eligibility.reason],
        )
    if eligibility.status == ReturnEligibilityStatus.INELIGIBLE:
        if "超过" in eligibility.reason:
            return _result(
                AfterSalesRuleDecision.MANUAL_REVIEW,
                60,
                ["R-WINDOW-EXPIRED"],
                ["已超过无理由退货期，需要判断是否属于质量或保修问题"],
            )
        return _result(
            AfterSalesRuleDecision.REJECT,
            0,
            ["R-ELIGIBILITY-REJECTED"],
            [eligibility.reason],
        )

    required_evidence = set(GENERAL_EVIDENCE)
    if set(context.product_categories) & ACTIVATED_DEVICE_CATEGORIES:
        required_evidence.update(ACTIVATED_DEVICE_EVIDENCE)
    missing = sorted(required_evidence - set(context.evidence.submitted_types))
    if missing:
        return _result(
            AfterSalesRuleDecision.REQUEST_MORE_EVIDENCE,
            10,
            ["R-EVIDENCE-INCOMPLETE"],
            ["退货预审材料不完整"],
            missing_evidence=missing,
        )
    if context.evidence.serial_number_matches is False:
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            90,
            ["R-SERIAL-MISMATCH"],
            ["提交的序列号与订单商品不一致"],
            conflicts=["submitted_serial_number != order_serial_number"],
        )
    if context.evidence.conflicts:
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            70,
            ["R-EVIDENCE-CONFLICT"],
            ["多来源证据存在冲突"],
            conflicts=context.evidence.conflicts,
        )
    if (
        context.evidence.visual_confidence is not None
        and context.evidence.visual_confidence < 0.85
    ):
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            50,
            ["R-VISUAL-LOW-CONFIDENCE"],
            ["图片识别置信度不足"],
        )
    if context.refund_amount_cents >= HARD_REVIEW_AMOUNT_CENTS:
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            60,
            ["R-HIGH-VALUE"],
            ["退款金额超过自动审批上限"],
        )

    risk_score = 0
    matched_rules: list[str] = []
    reasons: list[str] = []
    if context.refund_amount_cents > AUTO_REVIEW_AMOUNT_CENTS:
        risk_score += 20
        matched_rules.append("R-AMOUNT-ELEVATED")
        reasons.append("退款金额超过低风险自动处理阈值")
    if context.recent_return_count > 3:
        risk_score += 25
        matched_rules.append("R-FREQUENT-RETURNS")
        reasons.append("近期退货次数较多")
    if set(context.product_categories) & ACTIVATED_DEVICE_CATEGORIES:
        risk_score += 30
        matched_rules.append("R-ACTIVATED-DEVICE")
        reasons.append("商品属于需要核验激活状态的品类")
    if risk_score >= 20:
        return _result(
            AfterSalesRuleDecision.MANUAL_REVIEW,
            risk_score,
            matched_rules,
            reasons,
        )
    return _result(
        AfterSalesRuleDecision.AUTO_AUTHORIZE_RETURN,
        risk_score,
        ["R-LOW-RISK-AUTO-AUTHORIZE"],
        ["资格有效、材料完整且未命中风险规则，可自动允许寄回"],
    )


def _result(
    decision: AfterSalesRuleDecision,
    risk_score: int,
    matched_rules: list[str],
    reasons: list[str],
    *,
    missing_evidence: list[str] | None = None,
    conflicts: list[str] | None = None,
) -> AfterSalesRuleResult:
    risk_level = "high" if risk_score >= 50 else "medium" if risk_score >= 20 else "low"
    return AfterSalesRuleResult(
        decision=decision,
        risk_score=risk_score,
        risk_level=risk_level,
        matched_rules=matched_rules,
        reasons=reasons,
        missing_evidence=missing_evidence or [],
        conflicts=conflicts or [],
    )
