from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


EXPECTED_TABLES = {
    "products",
    "orders",
    "order_items",
    "service_policies",
    "conversation_turns",
    "conversation_turn_evidence",
    "users",
    "order_owners",
    "conversation_owners",
    "user_preferences",
}


def test_initial_migration_builds_and_removes_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", database_url)

    project_root = Path(__file__).resolve().parents[1]
    config = Config(project_root / "alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())

    command.downgrade(config, "base")
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()


def test_postgres_fts_migration_is_skipped_by_sqlite(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-fts.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", database_url)

    project_root = Path(__file__).resolve().parents[1]
    config = Config(project_root / "alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    product_columns = {column["name"] for column in inspect(engine).get_columns("products")}
    assert "search_vector" not in product_columns
    engine.dispose()
