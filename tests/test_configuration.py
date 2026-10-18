from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.config.settings import Settings
import app.db.database as db_mod


def test_settings_isolation_without_env_file(monkeypatch):
    """Case 1: Settings(_env_file=None) with no MySQL credentials reports has_database_credentials == False."""
    monkeypatch.delenv("MYSQL_USER", raising=False)
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)

    settings = Settings(_env_file=None)
    assert settings.mysql_user is None
    assert settings.mysql_password is None
    assert settings.has_database_credentials is False

    with pytest.raises(RuntimeError) as exc_info:
        _ = settings.async_database_url
    assert "Database credentials are not configured" in str(exc_info.value)


def test_session_creation_fails_safely_when_unconfigured(monkeypatch):
    """Case 2: The unconfigured session callable raises RuntimeError without attempting network connection."""
    monkeypatch.setattr(db_mod, "_session_factory", None)
    from app.db.database import _AsyncSessionLocalCallable

    unconfigured_session = _AsyncSessionLocalCallable()
    with pytest.raises(RuntimeError) as exc_info:
        unconfigured_session()
    assert "Database credentials are not configured" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_db_fails_safely_when_credentials_missing(monkeypatch):
    """Case 3: get_db() raises HTTP 503 when database credentials are missing."""
    monkeypatch.setattr(db_mod.settings, "mysql_user", None)
    monkeypatch.setattr(db_mod.settings, "mysql_password", None)
    monkeypatch.setattr(db_mod, "_session_factory", None)

    with pytest.raises(HTTPException) as exc_info:
        async for _ in db_mod.get_db():
            pass

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Database is unavailable"


@pytest.mark.anyio
async def test_health_endpoint_independent_of_db(monkeypatch):
    """Case 4: GET /health returns HTTP 200 in a controlled environment without MySQL credentials."""
    monkeypatch.setattr(db_mod.settings, "mysql_user", None)
    monkeypatch.setattr(db_mod.settings, "mysql_password", None)
    monkeypatch.setattr(db_mod, "engine", None)
    monkeypatch.setattr(db_mod, "_session_factory", None)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_database_health_fails_safely_without_credentials(monkeypatch):
    """Case 5: GET /health/database returns HTTP 503 without credentials and makes no connection attempt."""
    monkeypatch.setattr(db_mod.settings, "mysql_user", None)
    monkeypatch.setattr(db_mod.settings, "mysql_password", None)
    monkeypatch.setattr(db_mod, "engine", None)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health/database")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database is unavailable"}


def test_environment_variable_precedence_over_env_file(tmp_path, monkeypatch):
    """Case 6: Explicit OS environment variables override .env values using a temporary test config."""
    dummy_env = tmp_path / "test_dummy.env"
    dummy_env.write_text(
        "MYSQL_HOST=10.0.0.1\n"
        "MYSQL_PORT=3307\n"
        "MYSQL_DATABASE=file_db\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MYSQL_HOST", "192.168.1.50")
    monkeypatch.setenv("MYSQL_PORT", "4000")

    settings = Settings(_env_file=dummy_env)

    assert settings.mysql_host == "192.168.1.50"
    assert settings.mysql_port == 4000
    assert settings.mysql_database == "file_db"
