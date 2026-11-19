"""Add idempotent return-request work orders.

Revision ID: 20261115_0008
Revises: 20261115_0007
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261115_0008"
down_revision: str | None = "20261115_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "return_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'cancelled')",
            name="return_requests_status_valid",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "return_requests_by_user",
        "return_requests",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("return_requests_by_user", table_name="return_requests")
    op.drop_table("return_requests")
