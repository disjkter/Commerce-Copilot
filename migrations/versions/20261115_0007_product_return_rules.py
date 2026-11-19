"""Add deterministic per-product return rules.

Revision ID: 20261115_0007
Revises: 20261115_0006
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261115_0007"
down_revision: str | None = "20261115_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "returnable",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "return_window_days",
            sa.Integer(),
            server_default="7",
            nullable=False,
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "products_return_window_days_positive",
            "products",
            "return_window_days > 0",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "products_return_window_days_positive",
            "products",
            type_="check",
        )
    op.drop_column("products", "return_window_days")
    op.drop_column("products", "returnable")
