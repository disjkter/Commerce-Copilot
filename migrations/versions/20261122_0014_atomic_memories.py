"""Add provenance-aware atomic memory items.

Revision ID: 20261122_0014
Revises: 20261121_0013
Create Date: 2026-11-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261122_0014"
down_revision: str | None = "20261121_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("memory_scope", sa.String(length=32), nullable=False),
        sa.Column("memory_key", sa.String(length=128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_turn_id", sa.Integer(), nullable=True),
        sa.Column("evidence_reference", sa.String(length=256), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("supersedes_memory_id", sa.String(length=64), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="memory_items_confidence_valid",
        ),
        sa.CheckConstraint(
            "memory_type IN ('semantic', 'episodic', 'procedural')",
            name="memory_items_type_valid",
        ),
        sa.CheckConstraint(
            "memory_scope IN ('conversation', 'user')",
            name="memory_items_scope_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invalidated')",
            name="memory_items_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation_owners.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id"],
            ["conversation_turns.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id"),
    )
    op.create_index(
        "memory_items_active_user_key",
        "memory_items",
        ["user_id", "status", "memory_key"],
    )
    op.create_index(
        "memory_items_active_conversation",
        "memory_items",
        ["conversation_id", "status"],
    )
    op.create_index(
        "memory_items_active_user_key_unique",
        "memory_items",
        ["user_id", "memory_key"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND memory_scope = 'user'"
        ),
        sqlite_where=sa.text("status = 'active' AND memory_scope = 'user'"),
    )
    op.create_index(
        "memory_items_active_conversation_key_unique",
        "memory_items",
        ["user_id", "conversation_id", "memory_key"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND memory_scope = 'conversation'"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND memory_scope = 'conversation'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "memory_items_active_conversation_key_unique",
        table_name="memory_items",
    )
    op.drop_index("memory_items_active_user_key_unique", table_name="memory_items")
    op.drop_index("memory_items_active_conversation", table_name="memory_items")
    op.drop_index("memory_items_active_user_key", table_name="memory_items")
    op.drop_table("memory_items")
