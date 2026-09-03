"""Forward migrations.

Regression: the column migration used to live in server/main.py, so it only ran
when the API started. Any other entry point, tools/add_user.py in particular,
opened the same database without applying it and then failed on the first query
touching a column the file did not have yet.
"""

import pytest
import sqlalchemy as sa

from server.db import engine
from server.migrate import _ADDED_COLUMNS, ensure_schema


def columns_of(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


def test_every_managed_column_exists_after_ensure_schema(api_key):
    ensure_schema()
    for table, expected in _ADDED_COLUMNS.items():
        present = columns_of(table)
        for name in expected:
            assert name in present, f"{table}.{name} missing after ensure_schema"


def test_a_database_missing_a_column_gets_it_back(api_key):
    """Simulates opening a database created before the column existed."""
    with engine.begin() as conn:
        conn.execute(sa.text("alter table clips drop column version"))
    assert "version" not in columns_of("clips")

    ensure_schema()
    assert "version" in columns_of("clips")


def test_migration_is_repeatable(api_key):
    """Runs on every start, so a second pass must not fail on existing columns."""
    ensure_schema()
    ensure_schema()
    assert "favorite" in columns_of("clips")


def test_the_orm_can_query_after_a_migration(api_key):
    """The failure this actually prevents: the models are ahead of the file, so
    a plain select names a column that does not exist and raises."""
    from server.models import Clip

    with engine.begin() as conn:
        conn.execute(sa.text("alter table clips drop column favorite"))

    ensure_schema()
    with engine.connect() as conn:
        conn.execute(sa.select(Clip)).all()  # would raise OperationalError


def test_a_non_sqlite_database_url_names_the_setting(monkeypatch):
    """Regression: the only sign of a leftover Postgres URL was
    ModuleNotFoundError for a driver nobody had asked for, several frames from
    the setting that caused it."""
    import importlib

    import server.config
    import server.db

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@127.0.0.1/db")
    server.config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError) as raised:
            importlib.reload(server.db)
        message = str(raised.value)
        assert "postgresql" in message
        assert "SQLite" in message
        assert "pw" not in message, "the password must not be echoed back"
    finally:
        monkeypatch.undo()
        server.config.get_settings.cache_clear()
        importlib.reload(server.db)
