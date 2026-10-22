"""Add pgvector-backed product embeddings.

Revision ID: 20261022_0004
Revises: 20261022_0003
Create Date: 2026-10-22
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20261022_0004"
down_revision: str | None = "20261022_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'vector'
            ) THEN
                RAISE EXCEPTION
                    'pgvector extension is required before this migration'
                    USING HINT = 'Run scripts/enable_pgvector.ps1 as the postgres administrator.';
            END IF;
        END
        $$
        """
    )
    op.execute("ALTER TABLE products ADD COLUMN embedding vector(1024)")
    op.execute("ALTER TABLE products ADD COLUMN embedding_model varchar(128)")
    op.execute(
        "ALTER TABLE products ADD COLUMN embedding_source_hash varchar(64)"
    )
    op.execute(
        "ALTER TABLE products ADD COLUMN embedding_updated_at timestamptz"
    )
    op.execute(
        """
        CREATE INDEX products_embedding_hnsw
        ON products
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS products_embedding_hnsw")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding_updated_at")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding_source_hash")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding")
