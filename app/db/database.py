import logging

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import settings

logger = logging.getLogger(__name__)

if settings.has_database_credentials:
    DATABASE_URL = settings.async_database_url
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        connect_args={
            "connect_timeout": 5,
        },
    )
    _session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
else:
    DATABASE_URL = None
    engine = None
    _session_factory = None


class _AsyncSessionLocalCallable:
    """Callable proxy for AsyncSessionLocal to support lazy and mock invocation."""

    def __call__(self, *args, **kwargs) -> AsyncSession:
        if _session_factory is None:
            raise RuntimeError(
                "Database credentials are not configured. Cannot create a database session."
            )
        return _session_factory(*args, **kwargs)


AsyncSessionLocal = _session_factory if _session_factory is not None else _AsyncSessionLocalCallable()


class Base(DeclarativeBase):
    pass


async def get_db():
    """Yield a database session and always close it.

    Fails safely with HTTP 503 if database credentials are not configured.
    """
    if not settings.has_database_credentials or _session_factory is None:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable",
        )
    async with _session_factory() as session:
        yield session


async def check_database_connection() -> dict[str, str]:
    """Verify MySQL connectivity without exposing credentials."""
    if not settings.has_database_credentials or engine is None:
        raise SQLAlchemyError("Database credentials are not configured")
    try:
        async with engine.connect() as connection:
            database_name = await connection.scalar(
                text("SELECT DATABASE()")
            )
            account_name = await connection.scalar(
                text("SELECT CURRENT_USER()")
            )

        return {
            "database": str(database_name),
            "account": str(account_name),
            "status": "connected",
        }

    except SQLAlchemyError:
        logger.exception("Database connectivity check failed")
        raise


async def close_database() -> None:
    """Release pooled connections during application shutdown."""
    if engine is not None:
        await engine.dispose()
