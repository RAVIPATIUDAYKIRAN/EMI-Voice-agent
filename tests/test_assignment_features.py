import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import httpx
from sqlalchemy import select, text

from app.db.database import AsyncSessionLocal, close_database
from app.main import app
from app.models import Call, ConversationMessage, Customer, EMI
from app.services.commitment_service import CommitmentExtraction


# ===========================================================================
# 1. Customer and EMI Retrieval Tests (Requirement 4.A)
# ===========================================================================

@pytest.mark.anyio
async def test_get_customer_emi_valid():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Customer 1 (Rahul Sharma)
        res = await client.get("/customers/1/emi")
        assert res.status_code == 200
        data = res.json()
        assert data["customer_id"] == 1
        assert "Rahul Sharma" in data["full_name"]
        assert len(data["emis"]) >= 1
        assert data["emis"][0]["emi_amount"] > 0
        assert "due_date" in data["emis"][0]
        assert data["emis"][0]["status"] in {"PENDING", "OVERDUE"}


@pytest.mark.anyio
async def test_get_customer_emi_errors():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid ID
        res_invalid = await client.get("/customers/-5/emi")
        assert res_invalid.status_code == 422

        res_zero = await client.get("/customers/0/emi")
        assert res_zero.status_code == 422

        # Non-existent customer
        res_nonexistent = await client.get("/customers/999999/emi")
        assert res_nonexistent.status_code == 404


# ===========================================================================
# 2. Simulated Outbound Call Initiation (Requirement 4.B)
# ===========================================================================

@pytest.mark.anyio
async def test_start_simulated_call():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/customers/1/call")
        assert res.status_code == 201
        data = res.json()
        assert "call_id" in data
        assert data["customer_id"] == 1
        assert data["call_status"] == "INITIATED"
        call_id = data["call_id"]

        try:
            # Verify call row in DB
            async with AsyncSessionLocal() as db:
                call = await db.get(Call, call_id)
                assert call is not None
                assert call.call_status == "INITIATED"
                assert call.payment_commitment_confirmed is False
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_start_call_errors():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Nonexistent customer
        res = await client.post("/customers/999999/call")
        assert res.status_code == 404

        # Invalid customer ID
        res_inv = await client.post("/customers/0/call")
        assert res_inv.status_code == 422


# ===========================================================================
# 3. Dedicated Commitment API Tests (Requirement 4.F)
# ===========================================================================

@pytest.mark.anyio
async def test_commitment_endpoint_confirmed():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a fresh call for Customer 2
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # Set up pending date and stored agent question first (security requirement)
            async with AsyncSessionLocal() as db:
                call = await db.get(Call, call_id)
                call.pending_payment_date = date(2026, 11, 15)
                msg = ConversationMessage(
                    call_id=call_id,
                    speaker="AGENT",
                    message_text="Can you please confirm if you will make the payment on 15 November 2026?",
                )
                db.add(msg)
                await db.commit()

            # 1. Valid explicit confirmation
            payload = {
                "call_id": call_id,
                "promised_payment_date": "2026-11-15",
                "reason": "Salary credit on 14th",
                "expected_salary_date": "2026-11-14",
                "is_confirmed": True,
                "confirmation_text": "Yes, I confirm I will pay on 15 November 2026.",
            }
            res = await client.post("/customers/2/commitment", json=payload)
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["payment_commitment_confirmed"] is True
            assert data["promised_payment_date"] == "2026-11-15"
            assert data["pending_payment_date"] is None
            assert data["call_status"] == "COMPLETED"

            # Check DB persistence
            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.payment_commitment_confirmed is True
                assert saved.promised_payment_date == date(2026, 11, 15)
                assert saved.call_status == "COMPLETED"

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_commitment_endpoint_unconfirmed_proposal():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # 1. Attempting to create or replace a date proposal via commitment endpoint is rejected with 409
            payload_rejected_date = {
                "call_id": call_id,
                "promised_payment_date": "2026-11-20",
                "reason": "Awaiting bonus",
                "is_confirmed": False,
            }
            res_rejected = await client.post("/customers/2/commitment", json=payload_rejected_date)
            assert res_rejected.status_code == 409
            assert "cannot be created or replaced" in res_rejected.json()["detail"].lower()

            # 2. Recording permitted non-date details without a date proposal succeeds with 200
            payload_permitted_details = {
                "call_id": call_id,
                "promised_payment_date": None,
                "reason": "Awaiting bonus",
                "is_confirmed": False,
            }
            res_ok = await client.post("/customers/2/commitment", json=payload_permitted_details)
            assert res_ok.status_code == 200
            data = res_ok.json()
            assert data["payment_commitment_confirmed"] is False
            assert data["promised_payment_date"] is None
            assert data["pending_payment_date"] is None

            # Check DB persistence
            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.payment_commitment_confirmed is False
                assert saved.pending_payment_date is None
                assert saved.promised_payment_date is None
                assert saved.reason == "Awaiting bonus"

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_commitment_endpoint_rejects_tentative_or_fake_confirmation():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # Set up pending date and stored agent question first (security requirement)
            async with AsyncSessionLocal() as db:
                call = await db.get(Call, call_id)
                call.pending_payment_date = date(2026, 11, 15)
                msg = ConversationMessage(
                    call_id=call_id,
                    speaker="AGENT",
                    message_text="Can you please confirm if you will make the payment on 15 November 2026?",
                )
                db.add(msg)
                await db.commit()

            # Claiming is_confirmed=True but confirmation_text is tentative
            payload_tentative = {
                "call_id": call_id,
                "promised_payment_date": "2026-11-15",
                "is_confirmed": True,
                "confirmation_text": "I will try to pay, but I cannot guarantee it.",
            }
            res_tentative = await client.post("/customers/2/commitment", json=payload_tentative)
            assert res_tentative.status_code == 400
            assert "does not confirm" in res_tentative.json()["detail"].lower()

            # Claiming is_confirmed=True without confirmation_text
            payload_no_text = {
                "call_id": call_id,
                "promised_payment_date": "2026-11-15",
                "is_confirmed": True,
                "confirmation_text": "",
            }
            res_no_text = await client.post("/customers/2/commitment", json=payload_no_text)
            assert res_no_text.status_code == 400

            # Mismatched customer ID
            payload_mismatch = {
                "call_id": call_id,
                "promised_payment_date": "2026-11-15",
                "is_confirmed": False,
            }
            res_mismatch = await client.post("/customers/1/commitment", json=payload_mismatch)
            assert res_mismatch.status_code == 409

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


# ===========================================================================
# 4. Dedicated Summary API Tests (Requirement 4.G)
# ===========================================================================

@pytest.mark.anyio
async def test_call_summary_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # 1. Post to summary endpoint
            res = await client.post(f"/calls/{call_id}/summary")
            assert res.status_code == 200
            data = res.json()
            assert data["call_id"] == call_id
            assert data["customer_id"] == 2
            assert "call_summary" in data
            assert len(data["call_summary"]) > 0

            # 2. Check idempotency on repeated request
            res2 = await client.post(f"/calls/{call_id}/summary")
            assert res2.status_code == 200
            assert res2.json()["call_summary"] == data["call_summary"]

            # 3. Check 404 for nonexistent call
            res_404 = await client.post("/calls/999999/summary")
            assert res_404.status_code == 404

            # 4. Check 422 for invalid call_id
            res_422 = await client.post("/calls/0/summary")
            assert res_422.status_code == 422

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


# ===========================================================================
# 5. Customer Scenarios Tests (Requirement 5)
# ===========================================================================

@pytest.mark.anyio
async def test_scenario_after_salary_intent():
    """Scenario 4: Customer says they will pay after salary arrives."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        call_id = call_res.json()["call_id"]

        try:
            mock_ext = CommitmentExtraction(
                reason="Waiting for salary credit.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="AFTER_SALARY",
                needs_confirmation=True,
            )
            agent_reply = "I understand you will pay after salary arrives. When do you expect your salary to be credited?"

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext)):
                res = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I will pay as soon as my salary arrives."},
                )
                assert res.status_code == 200
                data = res.json()
                assert data["extraction"]["intent"] == "AFTER_SALARY"
                assert data["commitment_status"] == "NOT_CONFIRMED"
                assert data["call_status"] == "INITIATED"

            # Check DB: no promised payment date, not confirmed
            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.payment_commitment_confirmed is False
                assert saved.promised_payment_date is None
                assert saved.pending_payment_date is None

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_scenario_refusal_unable_to_pay():
    """Scenario 5: Refusal or inability to pay."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        call_id = call_res.json()["call_id"]

        try:
            mock_ext = CommitmentExtraction(
                reason="Financial difficulties.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="UNABLE_TO_PAY",
                needs_confirmation=True,
            )
            agent_reply = "I understand your situation, Priya. We have noted that you are currently unable to pay. Thank you for your time."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext)):
                res = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I cannot pay right now."},
                )
                assert res.status_code == 200
                data = res.json()
                assert data["extraction"]["intent"] == "UNABLE_TO_PAY"
                assert data["commitment_status"] == "NOT_CONFIRMED"

            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.payment_commitment_confirmed is False
                assert saved.promised_payment_date is None

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_scenario_already_paid():
    """Scenario 6: Customer states already paid."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        call_id = call_res.json()["call_id"]

        try:
            mock_ext = CommitmentExtraction(
                reason="Paid yesterday online.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="ALREADY_PAID",
                needs_confirmation=True,
            )
            agent_reply = "Thank you Priya. We have recorded your statement that you already made the payment. Our team will verify it shortly."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext)):
                res = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I have already paid my EMI yesterday."},
                )
                assert res.status_code == 200
                data = res.json()
                assert data["extraction"]["intent"] == "ALREADY_PAID"

            # Ensure EMI is not silently marked PAID without authorized verification
            async with AsyncSessionLocal() as db:
                emi = await db.get(EMI, 2)
                assert emi.status in {"PENDING", "OVERDUE"}

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_scenario_wrong_number():
    """Scenario 7: Wrong number ends simulated call politely and completes."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        call_id = call_res.json()["call_id"]

        try:
            mock_ext = CommitmentExtraction(
                reason="Wrong person answered.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="WRONG_NUMBER",
                needs_confirmation=True,
            )
            agent_reply = "I apologize for the disturbance. We will update our records. Have a good day."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext)):
                res = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "You have reached the wrong person."},
                )
                assert res.status_code == 200
                data = res.json()
                assert data["extraction"]["intent"] == "WRONG_NUMBER"
                assert data["call_status"] == "COMPLETED"

            # Check DB state
            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.call_status == "COMPLETED"
                assert saved.payment_commitment_confirmed is False
                assert saved.pending_payment_date is None

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_scenario_unavailable_callback_requested():
    """Scenario 8: Customer unavailable / callback requested."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/1/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            mock_ext = CommitmentExtraction(
                reason="Customer is busy and requested a callback.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="UNAVAILABLE",
                needs_confirmation=True,
            )
            agent_reply = "I understand you are busy right now, Rahul. We will arrange to call you back later. Thank you, have a good day."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext)):
                res = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I am busy right now and cannot talk. Please call me later."},
                )
                assert res.status_code == 200
                data = res.json()
                assert data["extraction"]["intent"] == "UNAVAILABLE"
                assert data["call_status"] == "COMPLETED"
                assert data["commitment_status"] == "NOT_CONFIRMED"
                assert data["extraction"]["proposed_payment_date"] is None

            # Verify persisted DB state in a fresh session
            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.call_status == "COMPLETED"
                assert saved.payment_commitment_confirmed is False
                assert saved.promised_payment_date is None
                assert saved.pending_payment_date is None
                assert saved.ended_at is not None
                assert "callback" in (saved.reason or "").lower() or "busy" in (saved.reason or "").lower()
                assert "Intent: UNAVAILABLE" in (saved.call_summary or "")
                assert "Call status: COMPLETED" in (saved.call_summary or "")
                assert "Payment commitment status: NOT_CONFIRMED" in (saved.call_summary or "")

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_scenario_insufficient_funds_multi_turn_close():
    """Scenario 9: Customer has insufficient funds, agent asks optional question, customer declines, call completes."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/1/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # Turn 1: Customer states insufficient funds, agent gently asks optional follow-up
            mock_ext_1 = CommitmentExtraction(
                reason="Does not have enough money right now.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="INSUFFICIENT_FUNDS",
                needs_confirmation=True,
            )
            agent_reply_1 = "I completely understand your situation, Rahul. If there is an approximate date you might be able to pay, please let us know, or we can assist you with alternative options."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply_1)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext_1)):
                res_1 = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I cannot pay the EMI right now because I don't have enough money."},
                )
                assert res_1.status_code == 200
                data_1 = res_1.json()
                assert data_1["extraction"]["intent"] == "INSUFFICIENT_FUNDS"
                assert data_1["call_status"] == "INITIATED"
                assert data_1["commitment_status"] == "NOT_CONFIRMED"
                assert data_1["extraction"]["proposed_payment_date"] is None

            async with AsyncSessionLocal() as db:
                saved_1 = await db.get(Call, call_id)
                assert saved_1.call_status == "INITIATED"
                assert saved_1.payment_commitment_confirmed is False
                assert saved_1.promised_payment_date is None
                assert saved_1.pending_payment_date is None

            # Turn 2: Customer declines / indicates they cannot commit to a date
            mock_ext_2 = CommitmentExtraction(
                reason="Cannot commit to any payment date due to financial difficulties.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="INSUFFICIENT_FUNDS",
                needs_confirmation=True,
            )
            agent_reply_2 = "I understand, Rahul. We have noted that you are currently unable to commit to a date. Please contact our support team when your situation improves. Have a good day."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply_2)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext_2)):
                res_2 = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I cannot commit to any date right now, please don't ask."},
                )
                assert res_2.status_code == 200
                data_2 = res_2.json()
                assert data_2["extraction"]["intent"] == "INSUFFICIENT_FUNDS"
                assert data_2["call_status"] == "COMPLETED"
                assert data_2["commitment_status"] == "NOT_CONFIRMED"
                assert data_2["extraction"]["proposed_payment_date"] is None

            # Verify final persisted DB state
            async with AsyncSessionLocal() as db:
                saved_2 = await db.get(Call, call_id)
                assert saved_2.call_status == "COMPLETED"
                assert saved_2.payment_commitment_confirmed is False
                assert saved_2.promised_payment_date is None
                assert saved_2.pending_payment_date is None
                assert saved_2.ended_at is not None
                assert "Intent: INSUFFICIENT_FUNDS" in (saved_2.call_summary or "")
                assert "Call status: COMPLETED" in (saved_2.call_summary or "")
                assert "Payment commitment status: NOT_CONFIRMED" in (saved_2.call_summary or "")

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()


@pytest.mark.anyio
async def test_scenario_insufficient_funds_immediate_close():
    """Scenario 10: Customer cannot pay and indicates inability to commit, interaction closes politely immediately."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            mock_ext = CommitmentExtraction(
                reason="Insufficient funds, unable to commit to any date.",
                expected_salary_date=None,
                proposed_payment_date=None,
                intent="INSUFFICIENT_FUNDS",
                needs_confirmation=True,
            )
            agent_reply = "I understand your situation, Priya. We have recorded that you cannot commit to a date right now. Have a good day."

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=agent_reply)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=mock_ext)):
                res = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "I don't have enough money and I cannot commit to any date."},
                )
                assert res.status_code == 200
                data = res.json()
                assert data["extraction"]["intent"] == "INSUFFICIENT_FUNDS"
                assert data["call_status"] == "COMPLETED"
                assert data["commitment_status"] == "NOT_CONFIRMED"
                assert data["extraction"]["proposed_payment_date"] is None

            async with AsyncSessionLocal() as db:
                saved = await db.get(Call, call_id)
                assert saved.call_status == "COMPLETED"
                assert saved.payment_commitment_confirmed is False
                assert saved.promised_payment_date is None
                assert saved.pending_payment_date is None
                assert saved.ended_at is not None

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await db.commit()
