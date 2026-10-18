from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from app.config.settings import settings

from app.api.calls import start_simulated_call
from app.models import Call, Customer, EMI


class IsolatedCallSession:
    def __init__(self, customer, emis, prior_call=None):
        self.customer = customer
        self.emis = emis
        self.call = None
        self.prior_call = prior_call
        self.statements = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)
        query = str(statement)
        result = MagicMock()
        if "FROM customers" in query:
            result.scalar_one_or_none.return_value = self.customer
        elif "FROM calls" in query:
            result.scalar_one_or_none.return_value = self.prior_call
        elif "FROM emis" in query:
            eligible = [
                emi for emi in self.emis
                if emi.customer_id == self.customer.customer_id
                and emi.status in {"PENDING", "OVERDUE"}
            ]
            eligible.sort(key=lambda emi: (emi.due_date, emi.emi_id))
            result.scalar_one_or_none.return_value = eligible[0] if eligible else None
        else:
            raise AssertionError(f"Unexpected query: {query}")
        return result

    def add(self, record):
        assert isinstance(record, Call)
        record.call_id = 71
        self.call = record

    async def flush(self):
        return None

    async def refresh(self, record, attribute_names=None):
        return None


def _customer(customer_id=2):
    return Customer(
        customer_id=customer_id,
        full_name="Synthetic Customer",
        phone_number="+919876543212",
        preferred_language="English",
    )


def _emi(emi_id, customer_id, due_date, status, amount):
    return EMI(
        emi_id=emi_id,
        customer_id=customer_id,
        due_date=due_date,
        status=status,
        emi_amount=Decimal(str(amount)),
    )


@pytest.mark.anyio
async def test_call_selects_earliest_eligible_emi_with_id_tiebreak_and_persists_owner():
    customer = _customer(2)
    emis = [
        _emi(9, 2, date(2026, 12, 1), "PENDING", 9000),
        _emi(8, 2, date(2026, 10, 20), "OVERDUE", 6200),
        _emi(3, 2, date(2026, 10, 20), "PENDING", 6100),
        _emi(2, 3, date(2026, 10, 1), "PENDING", 4500),
        _emi(1, 2, date(2026, 9, 1), "PAID", 5000),
    ]
    db = IsolatedCallSession(customer, emis)

    response = await start_simulated_call(
        customer_id=2, channel="simulated", db=db
    )

    assert response.customer_id == 2
    assert response.emi_id == 3
    assert response.emi_amount == 6100.0
    assert db.call.customer_id == 2
    assert db.call.emi_id == 3
    assert db.commit.await_count == 1
    emi_query = str(db.statements[1])
    assert "emis.customer_id" in emi_query
    assert "emis.status IN" in emi_query
    assert "emis.due_date" in emi_query and "emis.emi_id" in emi_query


@pytest.mark.anyio
async def test_call_rejects_missing_customer_and_customer_without_eligible_emi():
    missing_customer_db = IsolatedCallSession(None, [])
    with pytest.raises(HTTPException) as missing:
        await start_simulated_call(
            customer_id=2, channel="simulated", db=missing_customer_db
        )
    assert missing.value.status_code == 404
    missing_customer_db.commit.assert_not_awaited()

    customer = _customer(2)
    no_eligible_emi_db = IsolatedCallSession(
        customer,
        [
            _emi(1, 2, date(2026, 9, 1), "PAID", 6200),
            _emi(2, 3, date(2026, 10, 1), "PENDING", 4500),
        ],
    )
    with pytest.raises(HTTPException) as no_emi:
        await start_simulated_call(
            customer_id=2, channel="simulated", db=no_eligible_emi_db
        )
    assert no_emi.value.status_code == 409
    no_eligible_emi_db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_voice_call_requires_allowlist_and_idempotency_key(monkeypatch):
    customer = _customer()
    db = IsolatedCallSession(customer, [_emi(2, 2, date(2026, 10, 20), "PENDING", 6200)])
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "a" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15550000000")
    monkeypatch.setattr(settings, "twilio_allowed_destinations", "+919876543212")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-openai-key"))
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr("dashboard-test-key-0123456789abcdef"))
    with pytest.raises(HTTPException) as invalid:
        await start_simulated_call(
            customer_id=2, channel="voice", idempotency_key=None,
            x_dashboard_key="dashboard-test-key-0123456789abcdef", db=db
        )
    assert invalid.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_voice_call_requires_strong_operations_key_before_database_access(monkeypatch):
    db = IsolatedCallSession(_customer(), [])
    monkeypatch.setattr(settings, "dashboard_api_key", None)
    with pytest.raises(HTTPException) as missing:
        await start_simulated_call(
            customer_id=2, channel="voice", idempotency_key=None,
            x_dashboard_key=None, db=db,
        )
    assert missing.value.status_code == 503
    assert db.statements == []

    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr("dashboard-test-key-0123456789abcdef"))
    with pytest.raises(HTTPException) as invalid:
        await start_simulated_call(
            customer_id=2, channel="voice", idempotency_key=None,
            x_dashboard_key="wrong", db=db,
        )
    assert invalid.value.status_code == 401
    assert db.statements == []


@pytest.mark.anyio
async def test_repeated_voice_idempotency_key_returns_existing_call_without_provider_call(monkeypatch):
    from unittest.mock import AsyncMock

    customer = _customer()
    key = "voice-test-idempotency-123"
    existing_call = Call(
        call_id=88, customer_id=2, emi_id=2, idempotency_key=key,
        call_status="INITIATED", payment_commitment_confirmed=False,
    )
    db = IsolatedCallSession(
        customer, [_emi(2, 2, date(2026, 10, 20), "PENDING", 6200)], existing_call
    )
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "a" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15550000000")
    monkeypatch.setattr(settings, "twilio_allowed_destinations", "+919876543212")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-openai-key"))
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr("dashboard-test-key-0123456789abcdef"))
    provider = AsyncMock()
    monkeypatch.setattr("app.api.calls.create_twilio_call", provider)

    response = await start_simulated_call(
        customer_id=2, channel="voice", idempotency_key=key,
        x_dashboard_key="dashboard-test-key-0123456789abcdef", db=db
    )
    assert response.call_id == 88
    assert response.customer_id == 2
    assert response.emi_id == 2
    assert response.emi_amount == 6200.0
    provider.assert_not_awaited()
    db.commit.assert_not_awaited()
