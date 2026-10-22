from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from commerce_copilot.config import get_settings
from commerce_copilot.database import metadata


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
database_url = os.getenv("ALEMBIC_DATABASE_URL") or settings.database_url
if not database_url:
    database_url = f"sqlite:///{settings.database_path.resolve().as_posix()}"
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = metadata


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Ignore PostgreSQL-only FTS objects managed by an explicit migration."""
    if reflected and type_ == "column" and name in {
        "search_vector",
        "embedding",
        "embedding_model",
        "embedding_source_hash",
        "embedding_updated_at",
    }:
        return False
    if reflected and type_ == "index" and name in {
        "products_search_vector_gin",
        "products_embedding_hnsw",
    }:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
