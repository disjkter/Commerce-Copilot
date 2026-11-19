"""Persist versioned return precheck decisions.

Revision ID: 20261115_0010
Revises: 20261115_0009
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261115_0010"
down_revision: str | None = "20261115_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "return_requests",
        sa.Column("precheck_decision", sa.String(length=64), server_default="manual_review", nullable=False),
    )
    op.add_column(
        "return_requests",
        sa.Column("risk_score", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "return_requests",
        sa.Column("risk_level", sa.String(length=16), server_default="low", nullable=False),
    )
    op.add_column(
        "return_requests",
        sa.Column("decision_reasons", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "return_requests",
        sa.Column("matched_rules", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "return_requests",
        sa.Column("missing_evidence", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "return_requests",
        sa.Column("rule_version", sa.String(length=64), server_default="legacy", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("return_requests", "rule_version")
    op.drop_column("return_requests", "missing_evidence")
    op.drop_column("return_requests", "matched_rules")
    op.drop_column("return_requests", "decision_reasons")
    op.drop_column("return_requests", "risk_level")
    op.drop_column("return_requests", "risk_score")
    op.drop_column("return_requests", "precheck_decision")
