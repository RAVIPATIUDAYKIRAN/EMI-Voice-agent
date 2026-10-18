"""Fail-closed isolation for tests that intentionally exercise SQL persistence.

Legacy API integration tests create and delete rows. They are disabled unless
an explicit TEST_DATABASE_URL names a database different from the app target.
"""

from __future__ import annotations

import os
import asyncio
from urllib.parse import unquote

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import settings


_DATABASE_TEST_MODULES = {
    "test_assignment_features.py",
    "test_pending_date_bug_regression.py",
    "test_db_connection.py",
}


def _configure_isolated_test_database() -> str | None:
    raw_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not raw_url:
        return None

    try:
        test_url = make_url(raw_url)
    except Exception as exc:
        raise pytest.UsageError("TEST_DATABASE_URL is not a valid SQLAlchemy URL") from exc

    if test_url.drivername != "mysql+asyncmy":
        raise pytest.UsageError("TEST_DATABASE_URL must use the mysql+asyncmy driver")
    if not all((test_url.host, test_url.database, test_url.username, test_url.password)):
        raise pytest.UsageError("TEST_DATABASE_URL must include host, database, and credentials")

    # Refuse the application's database name even if a hostname alias is used
    # in TEST_DATABASE_URL.
    same_target = test_url.database.casefold() == settings.mysql_database.casefold()
    if same_target:
        raise pytest.UsageError(
            "TEST_DATABASE_URL targets the configured application database; refusing database tests"
        )

    async def verify_database_identity() -> bool:
        engine = create_async_engine(test_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        try:
            async with engine.connect() as connection:
                actual_name = await connection.scalar(text("SELECT DATABASE()"))
            return actual_name == test_url.database
        finally:
            await engine.dispose()

    try:
        identity_matches = asyncio.run(verify_database_identity())
    except Exception:
        raise pytest.UsageError(
            "TEST_DATABASE_URL could not be safely connected for identity verification"
        ) from None
    if not identity_matches:
        raise pytest.UsageError("Connected database identity does not match TEST_DATABASE_URL")

    # Mutate the shared Settings object before test modules import app.db.database.
    # Password is only assigned in memory and is never included in diagnostics.
    settings.mysql_host = test_url.host
    settings.mysql_port = test_url.port or 3306
    settings.mysql_database = test_url.database
    settings.mysql_user = unquote(test_url.username)
    from pydantic import SecretStr

    settings.mysql_password = SecretStr(unquote(test_url.password))
    return test_url.database


_TEST_DATABASE_NAME = _configure_isolated_test_database()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if _TEST_DATABASE_NAME:
        return
    skip_db = pytest.mark.skip(
        reason="requires explicit TEST_DATABASE_URL for an isolated database (application DB is forbidden)"
    )
    for item in items:
        if item.path.name in _DATABASE_TEST_MODULES:
            item.add_marker(skip_db)
