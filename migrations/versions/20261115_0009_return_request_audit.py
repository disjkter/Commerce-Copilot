"""Add return-request state transition audit events.

Revision ID: 20261115_0009
Revises: 20261115_0008
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261115_0009"
down_revision: str | None = "20261115_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "return_request_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("return_request_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32)),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'system', 'reviewer')",
            name="return_request_events_actor_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["return_request_id"],
            ["return_requests.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "return_request_events_timeline",
        "return_request_events",
        ["return_request_id", "occurred_at", "id"],
    )
    op.execute(
        """
        INSERT INTO return_request_events
            (return_request_id, from_status, to_status, actor_type, actor_id, reason, occurred_at)
        SELECT rr.id, NULL, rr.status, 'system', u.user_id,
               '审计表上线时补录已有工单', rr.created_at
        FROM return_requests AS rr
        JOIN users AS u ON u.id = rr.user_id
        """
    )


def downgrade() -> None:
    op.drop_index(
        "return_request_events_timeline",
        table_name="return_request_events",
    )
    op.drop_table("return_request_events")
