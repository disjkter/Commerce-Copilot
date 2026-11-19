from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class OrderItem(BaseModel):
    product_id: str
    title: str
    quantity: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)
    returnable: bool = True
    return_window_days: int = Field(default=7, gt=0, le=365)


class OrderRecord(BaseModel):
    order_id: str
    status: str
    placed_at: datetime
    items: list[OrderItem] = Field(default_factory=list)


class ReturnRequestRecord(BaseModel):
    request_id: str
    order_id: str
    user_id: str
    status: Literal["pending_review", "approved", "rejected", "cancelled"]
    reason: str
    precheck_decision: str = "manual_review"
    risk_score: int = Field(default=0, ge=0)
    risk_level: str = "low"
    decision_reasons: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    rule_version: str = ""
    created_at: datetime
    updated_at: datetime


class PolicyRecord(BaseModel):
    topic: str
    title: str
    body: str
    effective_from: str


class TrackingEvent(BaseModel):
    provider_event_id: str
    status: str
    location: str | None = None
    description: str
    occurred_at: datetime


class ShipmentRecord(BaseModel):
    shipment_id: str
    order_id: str
    tracking_number: str
    carrier_code: str
    status: str
    estimated_delivery_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    events: list[TrackingEvent] = Field(default_factory=list)


class TrackingWebhookPayload(BaseModel):
    provider_event_id: str = Field(min_length=1, max_length=128)
    tracking_number: str = Field(min_length=8, max_length=128)
    status: Literal[
        "pending",
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "delivery_failed",
        "exception",
        "returning",
        "returned",
    ]
    location: str | None = Field(default=None, max_length=256)
    description: str = Field(min_length=1, max_length=1000)
    occurred_at: datetime
    estimated_delivery_at: datetime | None = None

    @field_validator("occurred_at", "estimated_delivery_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("logistics timestamps must include a timezone")
        return value


class LogisticsWebhookResponse(BaseModel):
    provider: str
    provider_event_id: str
    result: Literal[
        "processed",
        "processed_out_of_order",
        "duplicate",
        "shipment_not_found",
    ]


class ShipmentException(BaseModel):
    code: Literal["carrier_exception", "delivery_failed", "stalled"]
    reason: str
