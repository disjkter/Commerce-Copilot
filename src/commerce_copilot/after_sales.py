from datetime import datetime, timedelta, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

from .support import OrderRecord, ShipmentRecord


class ReturnEligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_REVIEW = "needs_review"


class ReturnEligibilityDecision(BaseModel):
    order_id: str
    status: ReturnEligibilityStatus
    reason: str
    evaluated_at: datetime
    delivered_at: datetime | None = None
    deadline_at: datetime | None = None
    return_window_days: int | None = Field(default=None, gt=0)


def evaluate_return_eligibility(
    order: OrderRecord,
    shipments: list[ShipmentRecord],
    *,
    now: datetime | None = None,
) -> ReturnEligibilityDecision:
    """Evaluate return eligibility with deterministic, auditable rules."""
    evaluated_at = _as_utc(now or datetime.now(timezone.utc))
    if order.status == "cancelled":
        return _decision(
            order,
            ReturnEligibilityStatus.INELIGIBLE,
            "订单已取消，不适用退货流程",
            evaluated_at,
        )
    if not order.items:
        return _decision(
            order,
            ReturnEligibilityStatus.NEEDS_REVIEW,
            "订单缺少商品明细，需要人工核验",
            evaluated_at,
        )
    non_returnable = [item.title for item in order.items if not item.returnable]
    if non_returnable:
        return _decision(
            order,
            ReturnEligibilityStatus.INELIGIBLE,
            f"订单包含不可退商品：{'、'.join(non_returnable)}",
            evaluated_at,
        )

    delivered_shipments = [
        shipment
        for shipment in shipments
        if shipment.status == "delivered" and shipment.delivered_at is not None
    ]
    if not delivered_shipments:
        if order.status == "delivered":
            return _decision(
                order,
                ReturnEligibilityStatus.NEEDS_REVIEW,
                "订单显示已送达，但缺少可信签收时间",
                evaluated_at,
            )
        return _decision(
            order,
            ReturnEligibilityStatus.INELIGIBLE,
            "订单尚未签收，当前不能发起签收后退货",
            evaluated_at,
        )
    if len(delivered_shipments) != len(shipments):
        return _decision(
            order,
            ReturnEligibilityStatus.NEEDS_REVIEW,
            "订单存在未签收包裹，需要按包裹和商品人工核验",
            evaluated_at,
        )

    delivered_at = max(
        _as_utc(shipment.delivered_at)
        for shipment in delivered_shipments
        if shipment.delivered_at is not None
    )
    window_days = min(item.return_window_days for item in order.items)
    deadline_at = delivered_at + timedelta(days=window_days)
    if evaluated_at <= deadline_at:
        return ReturnEligibilityDecision(
            order_id=order.order_id,
            status=ReturnEligibilityStatus.ELIGIBLE,
            reason=f"订单仍在签收后 {window_days} 天退货期内",
            evaluated_at=evaluated_at,
            delivered_at=delivered_at,
            deadline_at=deadline_at,
            return_window_days=window_days,
        )
    return ReturnEligibilityDecision(
        order_id=order.order_id,
        status=ReturnEligibilityStatus.INELIGIBLE,
        reason=f"订单已超过签收后 {window_days} 天退货期限",
        evaluated_at=evaluated_at,
        delivered_at=delivered_at,
        deadline_at=deadline_at,
        return_window_days=window_days,
    )


def _decision(
    order: OrderRecord,
    status: ReturnEligibilityStatus,
    reason: str,
    evaluated_at: datetime,
) -> ReturnEligibilityDecision:
    return ReturnEligibilityDecision(
        order_id=order.order_id,
        status=status,
        reason=reason,
        evaluated_at=evaluated_at,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
