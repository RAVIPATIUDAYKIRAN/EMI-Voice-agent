"""Opt-in real-LLM Hindi confirmation test; uses an in-memory DB session fake."""

import os
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest

from app.config.settings import settings
from app.db.database import get_db
from app.main import app
from app.models import Call, ConversationMessage, Customer, EMI


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1"
    or not settings.openai_api_key.get_secret_value().strip(),
    reason="Set RUN_LIVE_LLM_TESTS=1 and configure the actual LLM key to run this test",
)


@pytest.mark.anyio
async def test_real_configured_llm_hindi_salary_delay_and_confirmation_without_mysql():
    customer = Customer(
        customer_id=2,
        full_name="Synthetic Hindi Test Customer",
        phone_number="+919000000002",
        preferred_language="Hindi",
    )
    emi = EMI(
        emi_id=2,
        customer_id=2,
        emi_amount=Decimal("6200.00"),
        due_date=date(2026, 10, 20),
        status="PENDING",
    )
    state = {"call": None, "messages": []}
    identifiers = {"message": 1}

    class InMemorySession:
        async def execute(self, statement):
            query = str(statement)
            result = MagicMock()
            if "FROM customers" in query:
                result.scalar_one_or_none.return_value = customer
            elif "FROM calls" in query:
                result.scalar_one_or_none.return_value = state["call"]
            elif "FROM emis" in query:
                if "LIMIT" in query or "emis.emi_id =" in query:
                    result.scalar_one_or_none.return_value = emi
                else:
                    result.scalars.return_value.all.return_value = [emi]
            elif "FROM conversation_messages" in query:
                if "ORDER BY conversation_messages.message_id DESC" in query:
                    agents = [m for m in state["messages"] if m.speaker == "AGENT"]
                    result.scalar_one_or_none.return_value = agents[-1] if agents else None
                else:
                    result.scalars.return_value.all.return_value = list(state["messages"])
            else:
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        def add(self, record):
            if isinstance(record, Call):
                record.call_id = 900002
                state["call"] = record
            elif isinstance(record, ConversationMessage):
                record.message_id = identifiers["message"]
                identifiers["message"] += 1
                state["messages"].append(record)

        async def flush(self):
            return None

        async def refresh(self, record, attribute_names=None):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    session = InMemorySession()

    async def override_db():
        yield session

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post("/customers/2/call")
            assert started.status_code == 201, started.text
            call_id = started.json()["call_id"]
            assert call_id == state["call"].call_id
            assert started.json()["customer_id"] == 2
            assert started.json()["emi_id"] == 2
            assert started.json()["emi_amount"] == 6200.0

            first = await client.post(
                f"/calls/{call_id}/message",
                json={
                    "message": (
                        "मेरी सैलरी अभी नहीं आई है। मैं 25 अक्टूबर 2026 को "
                        "₹6200 की EMI का भुगतान कर सकता हूँ।"
                    )
                },
            )
            assert first.status_code == 200, first.text
            first_data = first.json()
            assert first_data["extraction_status"] == "success"
            assert first_data["commitment_status"] == "NOT_CONFIRMED"
            assert first_data["call_status"] == "INITIATED"
            assert first_data["extraction"]["proposed_payment_date"] == "2026-10-25"
            assert state["call"].pending_payment_date == date(2026, 10, 25), first_data["agent_reply"]
            assert state["call"].promised_payment_date is None
            assert state["call"].payment_commitment_confirmed is False
            assert "25 अक्टूबर 2026" in first_data["agent_reply"]
            assert "6200" in first_data["agent_reply"].replace(",", "")

            second = await client.post(
                f"/calls/{call_id}/message",
                json={
                    "message": (
                        "हाँ, मैं 25 अक्टूबर 2026 को ₹6200 की EMI का "
                        "भुगतान करने की पुष्टि करता हूँ।"
                    )
                },
            )
            assert second.status_code == 200, second.text
            second_data = second.json()
            assert second_data["commitment_status"] == "CONFIRMED"
            assert second_data["call_status"] == "COMPLETED"
            assert second_data["customer_id"] == 2
            assert second_data["emi_id"] == 2
            assert second_data["emi_amount"] == 6200.0
            assert state["call"].promised_payment_date == date(2026, 10, 25)
            assert state["call"].payment_commitment_confirmed is True
            assert state["call"].pending_payment_date is None

            commitment = await client.post(
                "/customers/2/commitment",
                json={
                    "call_id": call_id,
                    "promised_payment_date": "2026-10-25",
                    "is_confirmed": True,
                    "confirmation_text": (
                        "हाँ, मैं 25 अक्टूबर 2026 को ₹6200 की EMI का "
                        "भुगतान करने की पुष्टि करता हूँ।"
                    ),
                },
            )
            assert commitment.status_code == 200, commitment.text
            summary = await client.post(f"/calls/{call_id}/summary")
            assert summary.status_code == 200, summary.text
            assert summary.json()["payment_commitment_confirmed"] is True
            assert summary.json()["promised_payment_date"] == "2026-10-25"
            assert summary.json()["emi_id"] == 2
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
