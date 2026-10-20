"""Create the initial commerce schema.

Revision ID: 20261021_0001
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261021_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("review_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stock", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tags", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint("price_cents >= 0"),
        sa.CheckConstraint("rating BETWEEN 0 AND 5"),
        sa.CheckConstraint("review_count >= 0"),
        sa.CheckConstraint("stock >= 0"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("placed_at", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "service_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_answer", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.String(length=32),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index(
        "conversation_turns_by_conversation",
        "conversation_turns",
        ["conversation_id", "id"],
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.String(length=32),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index(
        "users_username_lower_unique",
        "users",
        [sa.text("lower(username)")],
        unique=True,
    )
    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0"),
        sa.CheckConstraint("unit_price_cents >= 0"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("order_id", "product_id"),
    )
    op.create_table(
        "conversation_turn_evidence",
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("reference", sa.String(length=256), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["conversation_turns.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("turn_id", "position"),
    )
    op.create_table(
        "order_owners",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("order_id", "user_id"),
    )
    op.create_table(
        "conversation_owners",
        sa.Column("conversation_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("conversation_owners")
    op.drop_table("order_owners")
    op.drop_table("conversation_turn_evidence")
    op.drop_table("order_items")
    op.drop_index("users_username_lower_unique", table_name="users")
    op.drop_table("users")
    op.drop_index(
        "conversation_turns_by_conversation",
        table_name="conversation_turns",
    )
    op.drop_table("conversation_turns")
    op.drop_table("service_policies")
    op.drop_table("orders")
    op.drop_table("products")
