"""Add PostgreSQL full-text search index for products.

Revision ID: 20261022_0003
Revises: 20261021_0002
Create Date: 2026-10-22
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20261022_0003"
down_revision: str | None = "20261021_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        ALTER TABLE products
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(tags, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(brand, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(category, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(description, '')), 'C')
        ) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX products_search_vector_gin
        ON products USING GIN (search_vector)
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS products_search_vector_gin")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS search_vector")
