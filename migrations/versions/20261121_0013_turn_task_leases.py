"""Add worker leases to asynchronous turn tasks.

Revision ID: 20261121_0013
Revises: 20261121_0012
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261121_0013"
down_revision: str | None = "20261121_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_tasks",
        sa.Column("worker_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "turn_tasks",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("turn_tasks", "lease_expires_at")
    op.drop_column("turn_tasks", "worker_id")
