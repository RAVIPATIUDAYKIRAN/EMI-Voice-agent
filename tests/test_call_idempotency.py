from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import IntegrityError

from app.api.calls import start_simulated_call
from app.config.settings import settings
from app.models import Call, Customer, EMI


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _MemoryDB:
    """Small persistence fake: no configured database connection is used."""

    def __init__(self):
        self.customer = SimpleNamespace(
            customer_id=41,
            full_name="Test Customer",
            phone_number="+15555550123",
        )
        self.emi = SimpleNamespace(
            emi_id=51,
            customer_id=41,
            emi_amount=6200,
            due_date=date(2026, 10, 25),
            status="PENDING",
        )
        self.calls = {}
        self.added = []
        self.next_id = 1
        self.fail_flush = False
        self.race_call_on_flush = None
        self.rollback_count = 0
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is Customer:
            return _Result(self.customer)
        if entity is EMI:
            return _Result(self.emi)
        if entity is Call:
            where = str(statement.whereclause)
            if "idempotency_key" in where:
                key = next(iter(statement.compile().params.values()))
                return _Result(self.calls.get(key))
        raise AssertionError(f"Unexpected query: {statement}")

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        if self.fail_flush:
            raise SQLAlchemyError("simulated transaction failure")
        for call in self.added:
            if self.race_call_on_flush is not None:
                winner = self.race_call_on_flush
                self.calls[winner.idempotency_key] = winner
                self.race_call_on_flush = None
                raise IntegrityError("insert", {}, RuntimeError("duplicate key"))
            if call.call_id is None:
                call.call_id = self.next_id
                self.next_id += 1

    async def refresh(self, _call, attribute_names=None):
        return None

    async def commit(self):
        for call in self.added:
            if call.idempotency_key:
                self.calls[call.idempotency_key] = call

    async def rollback(self):
        self.rollback_count += 1
        self.added.clear()


def _enable_voice(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_api_key", type("Secret", (), {"get_secret_value": lambda self: "x" * 40})())
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "1" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", type("Secret", (), {"get_secret_value": lambda self: "test-token"})())
    monkeypatch.setattr(settings, "twilio_from_number", "+15555550999")
    monkeypatch.setattr(settings, "twilio_allowed_destinations", "+15555550123")
    monkeypatch.setattr(settings, "openai_api_key", type("Secret", (), {"get_secret_value": lambda self: "test-key"})())


@pytest.mark.anyio
async def test_same_voice_idempotency_key_reuses_call_without_second_provider_request(monkeypatch):
    _enable_voice(monkeypatch)
    db = _MemoryDB()
    provider = AsyncMock(return_value="CA-test-call")
    monkeypatch.setattr("app.api.calls.create_twilio_call", provider)
    kwargs = dict(
        customer_id=41,
        channel="voice",
        idempotency_key="request-key-00000001",
        x_dashboard_key="x" * 40,
        db=db,
    )

    first = await start_simulated_call(**kwargs)
    second = await start_simulated_call(**kwargs)

    assert first.call_id == second.call_id
    assert first.customer_id == second.customer_id == 41
    assert first.emi_id == second.emi_id == 51
    assert first.emi_amount == second.emi_amount == 6200
    assert len(db.added) == 1
    provider.assert_awaited_once()


@pytest.mark.anyio
async def test_idempotency_key_cannot_be_reused_for_another_customer(monkeypatch):
    _enable_voice(monkeypatch)
    db = _MemoryDB()
    call = Call(
        call_id=17,
        customer_id=41,
        emi_id=51,
        idempotency_key="request-key-00000002",
        call_status="INITIATED",
    )
    db.calls[call.idempotency_key] = call
    provider = AsyncMock(return_value="CA-test-call")
    monkeypatch.setattr("app.api.calls.create_twilio_call", provider)

    with pytest.raises(HTTPException) as error:
        await start_simulated_call(
            customer_id=42,
            channel="voice",
            idempotency_key=call.idempotency_key,
            x_dashboard_key="x" * 40,
            db=db,
        )

    assert error.value.status_code == 409
    provider.assert_not_awaited()


@pytest.mark.anyio
async def test_transaction_failure_rolls_back_without_starting_provider_call(monkeypatch):
    _enable_voice(monkeypatch)
    db = _MemoryDB()
    db.fail_flush = True
    provider = AsyncMock(return_value="CA-test-call")
    monkeypatch.setattr("app.api.calls.create_twilio_call", provider)

    with pytest.raises(HTTPException) as error:
        await start_simulated_call(
            customer_id=41,
            channel="voice",
            idempotency_key="request-key-00000003",
            x_dashboard_key="x" * 40,
            db=db,
        )

    assert error.value.status_code == 503
    assert db.rollback_count == 1
    provider.assert_not_awaited()


@pytest.mark.anyio
async def test_provider_failure_is_not_retried_automatically_for_same_key(monkeypatch):
    _enable_voice(monkeypatch)
    db = _MemoryDB()
    provider = AsyncMock(side_effect=RuntimeError("provider outcome uncertain"))
    monkeypatch.setattr("app.api.calls.create_twilio_call", provider)
    args = dict(
        customer_id=41,
        channel="voice",
        idempotency_key="request-key-00000004",
        x_dashboard_key="x" * 40,
        db=db,
    )

    with pytest.raises(HTTPException) as error:
        await start_simulated_call(**args)
    assert error.value.status_code == 502
    existing = await start_simulated_call(**args)

    assert existing.call_status == "FAILED"
    assert len(db.added) == 1
    provider.assert_awaited_once()


@pytest.mark.anyio
async def test_unique_key_race_returns_winning_call_without_provider_dispatch(monkeypatch):
    _enable_voice(monkeypatch)
    db = _MemoryDB()
    winner = Call(
        call_id=29,
        customer_id=41,
        emi_id=51,
        idempotency_key="request-key-00000005",
        call_status="INITIATED",
    )
    db.race_call_on_flush = winner
    provider = AsyncMock(return_value="CA-test-call")
    monkeypatch.setattr("app.api.calls.create_twilio_call", provider)

    result = await start_simulated_call(
        customer_id=41,
        channel="voice",
        idempotency_key=winner.idempotency_key,
        x_dashboard_key="x" * 40,
        db=db,
    )

    assert result.call_id == 29
    assert result.customer_id == 41
    provider.assert_not_awaited()
