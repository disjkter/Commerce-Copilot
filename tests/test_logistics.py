import asyncio
from datetime import datetime, timedelta, timezone

from commerce_copilot.database import CatalogRepository
from commerce_copilot.logistics import (
    RepositoryLogisticsTool,
    detect_shipment_exception,
)
from commerce_copilot.security import hash_password
from commerce_copilot.support import TrackingWebhookPayload


def _seed_owned_order(repository: CatalogRepository) -> tuple[str, str]:
    repository.seed_products()
    repository.seed_support_data()
    owner = repository.create_user("shipment-owner", hash_password("password-123"))
    other = repository.create_user("shipment-other", hash_password("password-123"))
    assert repository.assign_order_to_user("ORD-2026-1001", owner.user_id)
    return owner.user_id, other.user_id


def test_order_shipments_include_chronological_events(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    owner_id, _ = _seed_owned_order(repository)

    shipments = repository.get_order_shipments(
        "ORD-2026-1001",
        user_id=owner_id,
    )

    assert len(shipments) == 1
    shipment = shipments[0]
    assert shipment.tracking_number == "SF202610150001"
    assert shipment.status == "in_transit"
    assert [event.status for event in shipment.events] == [
        "picked_up",
        "in_transit",
    ]
    assert shipment.events[-1].location == "上海转运中心"


def test_shipment_lookup_enforces_order_ownership(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    owner_id, other_id = _seed_owned_order(repository)

    assert (
        repository.get_shipment("SF202610150001", user_id=owner_id) is not None
    )
    assert repository.get_shipment("SF202610150001", user_id=other_id) is None
    assert (
        repository.get_order_shipments("ORD-2026-1001", user_id=other_id) == []
    )
    assert [
        shipment.tracking_number
        for shipment in repository.list_user_shipments(owner_id)
    ] == ["SF202610150001"]
    assert repository.list_user_shipments(other_id) == []


def test_repository_logistics_tool_keeps_agent_boundary_async(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    owner_id, _ = _seed_owned_order(repository)
    tool = RepositoryLogisticsTool(repository)

    shipments = asyncio.run(
        tool.get_order_shipments("ORD-2026-1001", user_id=owner_id)
    )
    events = asyncio.run(
        tool.get_tracking_events("SF202610150001", user_id=owner_id)
    )
    owned_shipments = asyncio.run(tool.list_user_shipments(user_id=owner_id))

    assert shipments[0].shipment_id == "SHP-2026-1001"
    assert [event.provider_event_id for event in events] == [
        "SF-EVT-1001-01",
        "SF-EVT-1001-02",
    ]
    assert owned_shipments[0].order_id == "ORD-2026-1001"


def test_webhook_event_is_idempotent_and_updates_latest_status(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    owner_id, _ = _seed_owned_order(repository)
    event = TrackingWebhookPayload(
        provider_event_id="SF-WEBHOOK-1001-03",
        tracking_number="SF202610150001",
        status="out_for_delivery",
        location="上海市浦东新区",
        description="快件正在派送",
        occurred_at=datetime(2026, 10, 18, 8, 30, tzinfo=timezone.utc),
        estimated_delivery_at=datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc),
    )

    assert repository.ingest_tracking_event("sf", event) == "processed"
    assert repository.ingest_tracking_event("sf", event) == "duplicate"
    shipment = repository.get_shipment("SF202610150001", user_id=owner_id)

    assert shipment is not None
    assert shipment.status == "out_for_delivery"
    assert shipment.estimated_delivery_at == datetime(
        2026, 10, 18, 18, 0, tzinfo=timezone.utc
    )
    assert len(shipment.events) == 3


def test_out_of_order_event_is_stored_without_regressing_status(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    owner_id, _ = _seed_owned_order(repository)
    delivered = TrackingWebhookPayload(
        provider_event_id="SF-WEBHOOK-DELIVERED",
        tracking_number="SF202610150001",
        status="delivered",
        location="上海市浦东新区",
        description="快件已签收",
        occurred_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
    )
    delayed_history = TrackingWebhookPayload(
        provider_event_id="SF-WEBHOOK-DELAYED",
        tracking_number="SF202610150001",
        status="in_transit",
        location="上海转运中心",
        description="迟到的历史轨迹",
        occurred_at=datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc),
    )

    assert repository.ingest_tracking_event("SF", delivered) == "processed"
    assert (
        repository.ingest_tracking_event("SF", delayed_history)
        == "processed_out_of_order"
    )
    shipment = repository.get_shipment("SF202610150001", user_id=owner_id)

    assert shipment is not None
    assert shipment.status == "delivered"
    assert shipment.delivered_at == datetime(
        2026, 10, 20, 12, 0, tzinfo=timezone.utc
    )
    assert shipment.events[-1].provider_event_id == "SF-WEBHOOK-DELIVERED"


def test_stalled_shipment_is_detected_from_latest_event_time(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    owner_id, _ = _seed_owned_order(repository)
    shipment = repository.get_shipment("SF202610150001", user_id=owner_id)

    assert shipment is not None
    issue = detect_shipment_exception(
        shipment,
        now=datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc),
        stalled_after=timedelta(hours=48),
    )

    assert issue is not None
    assert issue.code == "stalled"
