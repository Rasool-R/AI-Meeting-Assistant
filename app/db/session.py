from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def build_engine(database_url: str | None = None) -> Engine:
    resolved_url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if resolved_url.startswith(
        "sqlite") else {}
    return create_engine(resolved_url, connect_args=connect_args, future=True)


engine = build_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
