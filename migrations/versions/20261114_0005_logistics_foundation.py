"""Add logistics shipments, tracking events, and webhook inbox.

Revision ID: 20261114_0005
Revises: 20261022_0004
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261114_0005"
down_revision: str | None = "20261022_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipment_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("tracking_number", sa.String(length=128), nullable=False, unique=True),
        sa.Column("carrier_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("estimated_delivery_at", sa.String(length=32)),
        sa.Column("shipped_at", sa.String(length=32)),
        sa.Column("delivered_at", sa.String(length=32)),
        sa.Column(
            "updated_at",
            sa.String(length=32),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
    )
    op.create_index("shipments_by_order", "shipments", ["order_id", "id"])
    op.create_table(
        "shipment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("location", sa.String(length=256)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "shipment_id",
            "provider_event_id",
            name="shipment_events_provider_event_unique",
        ),
    )
    op.create_index(
        "shipment_events_timeline",
        "shipment_events",
        ["shipment_id", "occurred_at", "id"],
    )
    op.create_table(
        "logistics_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.String(length=32),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="logistics_webhook_provider_event_unique",
        ),
    )


def downgrade() -> None:
    op.drop_table("logistics_webhook_events")
    op.drop_index("shipment_events_timeline", table_name="shipment_events")
    op.drop_table("shipment_events")
    op.drop_index("shipments_by_order", table_name="shipments")
    op.drop_table("shipments")
