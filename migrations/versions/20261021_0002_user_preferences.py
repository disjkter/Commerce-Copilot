"""Add persistent user preferences.

Revision ID: 20261021_0002
Revises: 20261021_0001
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261021_0002"
down_revision: str | None = "20261021_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("max_budget_cents", sa.Integer(), nullable=True),
        sa.Column("preferred_categories", sa.JSON(), nullable=False),
        sa.Column("preferred_brands", sa.JSON(), nullable=False),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("priorities", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.String(length=32),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_budget_cents IS NULL OR max_budget_cents >= 0"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
