"""SQLAlchemy engine + session — PostgreSQL only.

Pool settings tuned for Railway / managed Postgres:
- pool_pre_ping: detects stale connections (managed PG kills idle conns)
- pool_recycle: recycle every 30 min to stay ahead of provider timeouts
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


# PostgreSQL gets pooling. SQLite (used in some test setups) cannot.
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_engine_kwargs: dict = {"future": True}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_pre_ping=True, pool_recycle=1800,
                          pool_size=5, max_overflow=10)

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
