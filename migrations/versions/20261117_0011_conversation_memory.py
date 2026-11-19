"""Add persistent structured conversation memory.

Revision ID: 20261117_0011
Revises: 20261115_0010
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261117_0011"
down_revision: str | None = "20261115_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_memories",
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("entities_json", sa.JSON(), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("compacted_through_turn_id", sa.Integer(), nullable=False),
        sa.Column("source_turn_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation_owners.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )


def downgrade() -> None:
    op.drop_table("conversation_memories")
