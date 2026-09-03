from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()

if not _settings.database_url.startswith("sqlite"):
    # Only the SQLite driver ships. Without this the failure is an import error
    # naming a driver nobody asked for, several frames from the setting that
    # actually caused it.
    scheme = _settings.database_url.split("://", 1)[0]
    raise RuntimeError(
        f"DATABASE_URL is set to {scheme!r}, but this build only supports SQLite. "
        "An install keeps its clips in a file beside its other data. "
        "Set DATABASE_URL=sqlite:///./recording.db, or remove it to take the default."
    )

_connect_args = {"check_same_thread": False}

engine = create_engine(
    _settings.database_url, connect_args=_connect_args, pool_pre_ping=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
