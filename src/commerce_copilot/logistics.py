import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .database import CatalogRepository
from .support import ShipmentException, ShipmentRecord, TrackingEvent


ACTIVE_SHIPMENT_STATUSES = {"picked_up", "in_transit", "out_for_delivery"}


class LogisticsTool(Protocol):
    """Stable boundary used by a future Logistics Agent.

    Implementations may read the local repository, call a carrier HTTP API, or
    delegate to an MCP server without changing the Agent-facing contract.
    """

    async def get_order_shipments(
        self,
        order_id: str,
        *,
        user_id: str,
    ) -> list[ShipmentRecord]: ...

    async def list_user_shipments(
        self,
        *,
        user_id: str,
        limit: int = 20,
    ) -> list[ShipmentRecord]: ...

    async def get_tracking_events(
        self,
        tracking_number: str,
        *,
        user_id: str,
    ) -> list[TrackingEvent]: ...

    async def get_shipment(
        self,
        tracking_number: str,
        *,
        user_id: str,
    ) -> ShipmentRecord | None: ...


class RepositoryLogisticsTool:
    """Read-only logistics tool backed by the application repository."""

    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    async def get_order_shipments(
        self,
        order_id: str,
        *,
        user_id: str,
    ) -> list[ShipmentRecord]:
        return await asyncio.to_thread(
            self.repository.get_order_shipments,
            order_id,
            user_id=user_id,
        )

    async def list_user_shipments(
        self,
        *,
        user_id: str,
        limit: int = 20,
    ) -> list[ShipmentRecord]:
        return await asyncio.to_thread(
            self.repository.list_user_shipments,
            user_id,
            limit=limit,
        )

    async def get_tracking_events(
        self,
        tracking_number: str,
        *,
        user_id: str,
    ) -> list[TrackingEvent]:
        shipment = await asyncio.to_thread(
            self.repository.get_shipment,
            tracking_number,
            user_id=user_id,
        )
        return shipment.events if shipment is not None else []

    async def get_shipment(
        self,
        tracking_number: str,
        *,
        user_id: str,
    ) -> ShipmentRecord | None:
        return await asyncio.to_thread(
            self.repository.get_shipment,
            tracking_number,
            user_id=user_id,
        )


def create_webhook_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    return hmac.compare_digest(signature, create_webhook_signature(body, secret))


def detect_shipment_exception(
    shipment: ShipmentRecord,
    *,
    now: datetime | None = None,
    stalled_after: timedelta = timedelta(hours=48),
) -> ShipmentException | None:
    if shipment.status == "delivery_failed":
        return ShipmentException(code="delivery_failed", reason="承运商反馈派送失败")
    if shipment.status == "exception":
        return ShipmentException(code="carrier_exception", reason="承运商标记物流异常")
    if shipment.status not in ACTIVE_SHIPMENT_STATUSES or not shipment.events:
        return None
    latest_at = shipment.events[-1].occurred_at
    if latest_at.tzinfo is None:
        latest_at = latest_at.replace(tzinfo=timezone.utc)
    active_now = now or datetime.now(timezone.utc)
    if active_now.tzinfo is None:
        active_now = active_now.replace(tzinfo=timezone.utc)
    if active_now.astimezone(timezone.utc) - latest_at.astimezone(timezone.utc) > stalled_after:
        return ShipmentException(
            code="stalled",
            reason=f"物流轨迹已超过 {int(stalled_after.total_seconds() // 3600)} 小时未更新",
        )
    return None
