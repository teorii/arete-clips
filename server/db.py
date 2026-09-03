from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()
_connect_args = (
    {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
)

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
