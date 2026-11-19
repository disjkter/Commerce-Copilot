"""Use timezone-aware timestamps for orders and logistics.

Revision ID: 20261115_0006
Revises: 20261114_0005
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20261115_0006"
down_revision: str | None = "20261114_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TIME_COLUMNS = (
    ("orders", "placed_at", False),
    ("shipments", "estimated_delivery_at", True),
    ("shipments", "shipped_at", True),
    ("shipments", "delivered_at", True),
    ("shipments", "updated_at", False),
    ("shipment_events", "occurred_at", False),
    ("logistics_webhook_events", "received_at", False),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # New SQLite test databases are created from timezone-aware metadata.
        return

    op.execute("ALTER TABLE shipments ALTER COLUMN updated_at DROP DEFAULT")
    op.execute(
        "ALTER TABLE logistics_webhook_events "
        "ALTER COLUMN received_at DROP DEFAULT"
    )
    for table, column, nullable in TIME_COLUMNS:
        source = f'NULLIF("{column}", \'\')' if nullable else f'"{column}"'
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
            "TYPE timestamptz "
            f"USING ({source}::timestamp AT TIME ZONE 'Asia/Shanghai')"
        )
    op.execute(
        "ALTER TABLE shipments ALTER COLUMN updated_at "
        "SET DEFAULT CURRENT_TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE logistics_webhook_events ALTER COLUMN received_at "
        "SET DEFAULT CURRENT_TIMESTAMP"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE shipments ALTER COLUMN updated_at DROP DEFAULT")
    op.execute(
        "ALTER TABLE logistics_webhook_events "
        "ALTER COLUMN received_at DROP DEFAULT"
    )
    for table, column, _ in reversed(TIME_COLUMNS):
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
            "TYPE varchar(32) "
            f"USING to_char(\"{column}\" AT TIME ZONE 'Asia/Shanghai', "
            "'YYYY-MM-DD HH24:MI:SS')"
        )
    local_now = (
        "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai', "
        "'YYYY-MM-DD HH24:MI:SS')"
    )
    op.execute(
        "ALTER TABLE shipments ALTER COLUMN updated_at "
        f"SET DEFAULT {local_now}"
    )
    op.execute(
        "ALTER TABLE logistics_webhook_events ALTER COLUMN received_at "
        f"SET DEFAULT {local_now}"
    )
