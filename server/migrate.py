"""Schema creation and the small forward migrations that follow it.

Anything opening the database needs this, not just the web app. A command line
tool that creates tables but skips pending column additions will happily run a
query built from models that are ahead of the file on disk, and fail on a
column that does not exist yet.

Alembic is the answer once this has real users and real data to protect. While
the schema is still moving, adding a missing column beats asking someone to
delete their clip library.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from .db import engine
from .models import Base

# Columns added after a database might already exist, with the type clause per
# dialect. SQLite has no boolean literal, hence the split.
_ADDED_COLUMNS: dict[str, dict[str, dict[str, str]]] = {
    "clips": {
        "favorite": {
            "sqlite": "boolean not null default 0",
            "*": "boolean not null default false",
        },
        "version": {
            "sqlite": "integer not null default 1",
            "*": "integer not null default 1",
        },
    },
}


def ensure_schema() -> None:
    """Create anything missing, then add columns to tables that predate them."""
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    dialect = engine.dialect.name
    for table, columns in _ADDED_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, clause in columns.items():
            if name in existing:
                continue
            spec = clause.get(dialect, clause["*"])
            with engine.begin() as conn:
                conn.execute(text(f"alter table {table} add column {name} {spec}"))
