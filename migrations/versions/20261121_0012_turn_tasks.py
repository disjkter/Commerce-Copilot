"""Add persistent asynchronous turn tasks.

Revision ID: 20261121_0012
Revises: 20261117_0011
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261121_0012"
down_revision: str | None = "20261117_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="turn_tasks_status_valid",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="turn_tasks_attempt_count_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="turn_tasks_user_idempotency_unique",
        ),
    )
    op.create_index(
        "turn_tasks_by_user_created",
        "turn_tasks",
        ["user_id", "created_at"],
    )
    op.create_index(
        "turn_tasks_pending_queue",
        "turn_tasks",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("turn_tasks_pending_queue", table_name="turn_tasks")
    op.drop_index("turn_tasks_by_user_created", table_name="turn_tasks")
    op.drop_table("turn_tasks")
