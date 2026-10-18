import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversations import extract_pending_payment_date
from app.db.database import AsyncSessionLocal, close_database
from app.main import app
from app.models import Call, ConversationMessage
from app.services.commitment_service import CommitmentExtraction
from app.services.confirmation_service import (
    ConfirmationStatus,
    verify_payment_confirmation,
)


# ===========================================================================
# 1. Unit-level confirmation service & extraction invariant tests
# ===========================================================================

def test_tentative_date_with_uncertainty_not_confirmed():
    """Scenario 1 & uncertainty precedence: tentative response must not confirm."""
    pending = date(2026, 10, 25)
    agent_q = "Would you be able to pay on 25 October 2026?"

    for response in (
        "My salary is delayed. I'll try to pay on 25 October 2026, but I cannot guarantee it.",
        "I'll try to pay on 25 October 2026",
        "Maybe. I cannot guarantee it",
        "Yes, but I cannot guarantee it",  # Uncertainty overrides 'yes'
        "Yes, I will try to pay on that date",
    ):
        result = verify_payment_confirmation(
            customer_response=response,
            proposed_payment_date=None,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert result.status == ConfirmationStatus.AMBIGUOUS, (
            f"Expected AMBIGUOUS for {response!r}, got {result.status}"
        )
        assert result.payment_date is None


def test_ambiguous_response():
    """Scenario 2: Ambiguous response to an agent confirmation question."""
    pending = date(2026, 10, 25)
    agent_q = "Can you confirm that you can pay on 25 October 2026?"

    for response in (
        "Maybe. I will see after my salary comes.",
        "Let me see",
        "Not sure yet",
        "I will see after my salary",
    ):
        result = verify_payment_confirmation(
            customer_response=response,
            proposed_payment_date=None,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert result.status == ConfirmationStatus.AMBIGUOUS, (
            f"Expected AMBIGUOUS for {response!r}, got {result.status}"
        )


def test_explicit_affirmative_confirmation():
    """Scenario 3: Explicit affirmative confirmation of the requested date."""
    pending = date(2026, 10, 25)
    agent_q = "Can you confirm that you can pay on 25 October 2026?"

    for response in (
        "Yes, I will pay on 25 October 2026.",
        "Yes, I will pay",
        "I confirm",
        "Confirmed",
        "I agree",
        "Yes, I can pay",
    ):
        result = verify_payment_confirmation(
            customer_response=response,
            proposed_payment_date=None,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert result.status == ConfirmationStatus.CONFIRMED, (
            f"Expected CONFIRMED for {response!r}, got {result.status}"
        )
        assert result.payment_date == pending


def test_explicit_rejection():
    """Scenario 4: Explicit rejection of the requested date."""
    pending = date(2026, 10, 25)
    agent_q = "Would you be able to pay on 25 October 2026?"

    for response in (
        "No, I cannot pay on that date.",
        "No",
        "I will not pay",
        "I cannot confirm",
        "Not on that date",
    ):
        result = verify_payment_confirmation(
            customer_response=response,
            proposed_payment_date=None,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert result.status == ConfirmationStatus.NOT_CONFIRMED, (
            f"Expected NOT_CONFIRMED for {response!r}, got {result.status}"
        )


def test_conflicting_date_response():
    """Scenario 5: Response containing a conflicting date."""
    pending = date(2026, 10, 25)
    agent_q = "Can you confirm that you can pay on 25 October 2026?"

    for response in (
        "No, I will pay on 2026-11-15 instead.",
        "I will pay on 15 November 2026",
    ):
        result = verify_payment_confirmation(
            customer_response=response,
            proposed_payment_date=None,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert result.status != ConfirmationStatus.CONFIRMED, (
            f"Conflicting date incorrectly confirmed: {response!r}"
        )


def test_first_message_no_prior_proposal():
    """Scenario 6: First customer message mentioning date without prior agent question."""
    result = verify_payment_confirmation(
        customer_response="My salary is delayed. I'll try to pay on 25 October 2026, but I cannot guarantee it.",
        proposed_payment_date=None,
        agent_last_message="",
        pending_proposal_date=None,
    )
    assert result.status == ConfirmationStatus.NO_PENDING_PROPOSAL


def test_agent_reply_with_due_date_not_mistaken():
    """Scenario 7: Agent reply containing EMI due date must not be mistaken for payment commitment."""
    # Acknowledgment of tentative date with polite non-payment question
    reply_bug = (
        "I understand, Priya. Thank you for letting us know; "
        "I understand that 25 October is tentative and not guaranteed. "
        "Would you be able to update us when you have a clearer idea of the payment timing?"
    )
    assert extract_pending_payment_date(reply_bug) is None

    # EMI due date statement followed by non-payment question
    reply_due_date = (
        "Your EMI of INR 6,200 was due on 10 October 2026. "
        "Would you be able to update us on your situation?"
    )
    assert extract_pending_payment_date(reply_due_date) is None

    # EMI due date statement followed by genuine payment question for a new date
    reply_genuine = (
        "Your EMI of INR 6,200 was due on 10 October 2026. "
        "Can you pay on 25 October 2026?"
    )
    assert extract_pending_payment_date(reply_genuine) == date(2026, 10, 25)


# ===========================================================================
# 2. End-to-end API & fresh-session persistence tests
# ===========================================================================

@pytest.mark.anyio
async def test_end_to_end_bug_scenario_persistence():
    """
    Scenarios 1, 6, 7, 9:
    Reproduce exact Priya Reddy scenario and verify fresh-session persistence:
    - Customer message: 'My salary is delayed. I'll try to pay on 25 October 2026, but I cannot guarantee it.'
    - Agent reply acknowledges tentative date without asking to confirm payment.
    - Verified DB state:
        pending_payment_date IS NULL
        promised_payment_date IS NULL
        payment_commitment_confirmed == 0
        call_status == 'INITIATED'
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a fresh call for Customer 2
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201, call_res.text
        call_data = call_res.json()
        fresh_call_id = call_data["call_id"]

        try:
            # 2. Mock AI reply to match exact agent reply from bug report
            agent_tentative_reply = (
                "I understand, Priya. Thank you for letting us know; "
                "I understand that 25 October is tentative and not guaranteed. "
                "Would you be able to update us when you have a clearer idea of the payment timing?"
            )
            mock_extraction = CommitmentExtraction(
                reason="Salary is delayed.",
                expected_salary_date=None,
                proposed_payment_date=date(2026, 10, 25),
                intent="SALARY_DELAY",
                needs_confirmation=True,
            )

            with patch(
                "app.api.conversations.generate_agent_reply",
                new=AsyncMock(return_value=agent_tentative_reply),
            ), patch(
                "app.api.conversations.extract_commitment_details",
                new=AsyncMock(return_value=mock_extraction),
            ):
                msg_res = await client.post(
                    f"/calls/{fresh_call_id}/message",
                    json={
                        "message": "My salary is delayed. I'll try to pay on 25 October 2026, but I cannot guarantee it."
                    },
                )
                assert msg_res.status_code == 200, msg_res.text
                body = msg_res.json()

                # API response assertions
                assert body["call_id"] == fresh_call_id
                assert body["call_status"] == "INITIATED"
                assert body["commitment_status"] == "NOT_CONFIRMED"
                assert body["extraction"]["proposed_payment_date"] == "2026-10-25"

            # 3. Read directly from a fresh database session
            async with AsyncSessionLocal() as fresh_db:
                fresh_call = await fresh_db.get(Call, fresh_call_id)
                assert fresh_call is not None
                assert fresh_call.pending_payment_date is None, (
                    f"Expected pending_payment_date to be NULL, got {fresh_call.pending_payment_date}"
                )
                assert fresh_call.promised_payment_date is None
                assert fresh_call.payment_commitment_confirmed is False
                assert fresh_call.call_status == "INITIATED"
                assert "Pending payment date" not in (fresh_call.call_summary or "")

        finally:
            # Cleanup only this test call's records
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(
                    text("DELETE FROM conversation_messages WHERE call_id = :cid"),
                    {"cid": fresh_call_id},
                )
                await cleanup_db.execute(
                    text("DELETE FROM calls WHERE call_id = :cid"),
                    {"cid": fresh_call_id},
                )
                await cleanup_db.commit()


@pytest.mark.anyio
async def test_subsequent_message_after_tentative_flow():
    """
    Scenario 8 & 3:
    Subsequent turn after tentative response:
    Turn 1: Tentative response -> pending is NULL.
    Turn 2: Customer provides date -> Agent asks confirmation -> pending is set.
    Turn 3: Customer explicitly confirms -> promised_payment_date set, confirmed=True, call_status='COMPLETED'.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        fresh_call_id = call_res.json()["call_id"]

        try:
            # Turn 1: Tentative message
            reply1 = "I understand that is tentative. Would you be able to update us when clear?"
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply1)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                r1 = await client.post(
                    f"/calls/{fresh_call_id}/message",
                    json={"message": "I will try to pay on 25 October 2026, but not sure."},
                )
                assert r1.status_code == 200

            async with AsyncSessionLocal() as db:
                c1 = await db.get(Call, fresh_call_id)
                assert c1.pending_payment_date is None
                assert c1.payment_commitment_confirmed is False

            # Turn 2: Customer gives date, Agent asks confirmation
            reply2 = "Thank you Priya. Would you be able to pay on 28 October 2026?"
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply2)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                r2 = await client.post(
                    f"/calls/{fresh_call_id}/message",
                    json={"message": "I checked with accounts, salary is credited on 27 October."},
                )
                assert r2.status_code == 200

            async with AsyncSessionLocal() as db:
                c2 = await db.get(Call, fresh_call_id)
                assert c2.pending_payment_date == date(2026, 10, 28)
                assert c2.payment_commitment_confirmed is False
                assert c2.call_status == "INITIATED"

            # Turn 3: Customer explicitly confirms
            reply3 = "Thank you Priya. Your payment commitment for 28 October 2026 has been noted. Have a great day!"
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply3)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                r3 = await client.post(
                    f"/calls/{fresh_call_id}/message",
                    json={"message": "Yes, I will pay on 28 October 2026."},
                )
                assert r3.status_code == 200
                assert r3.json()["commitment_status"] == "CONFIRMED"
                assert r3.json()["call_status"] == "COMPLETED"

            async with AsyncSessionLocal() as db:
                c3 = await db.get(Call, fresh_call_id)
                assert c3.payment_commitment_confirmed is True
                assert c3.promised_payment_date == date(2026, 10, 28)
                assert c3.pending_payment_date is None
                assert c3.call_status == "COMPLETED"

        finally:
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": fresh_call_id})
                await cleanup_db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": fresh_call_id})
                await cleanup_db.commit()


@pytest.mark.anyio
async def test_failure_rollback_no_partial_commitment():
    """
    Scenario 10:
    A failure during processing must roll back and not leave a partially updated commitment.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/2/call")
        assert call_res.status_code == 201
        fresh_call_id = call_res.json()["call_id"]

        try:
            # Simulate an unhandled error inside generate_agent_reply
            with patch(
                "app.api.conversations.generate_agent_reply",
                new=AsyncMock(side_effect=RuntimeError("Simulated LLM outage")),
            ):
                res = await client.post(
                    f"/calls/{fresh_call_id}/message",
                    json={"message": "Yes, I will pay on 25 October 2026."},
                )
                assert res.status_code == 502

            # Assert database state remains pristine (rolled back)
            async with AsyncSessionLocal() as db:
                c = await db.get(Call, fresh_call_id)
                assert c.payment_commitment_confirmed is False
                assert c.promised_payment_date is None
                assert c.pending_payment_date is None
                assert c.call_status == "INITIATED"

                # Ensure customer message was not committed
                msg_count = await db.scalar(
                    select(text("COUNT(*)")).select_from(ConversationMessage).where(ConversationMessage.call_id == fresh_call_id)
                )
                assert msg_count == 0

        finally:
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": fresh_call_id})
                await cleanup_db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": fresh_call_id})
                await cleanup_db.commit()


# ===========================================================================
# 3. Dedicated Regression Tests for User Reproduction & Strict Requirements
# ===========================================================================

@pytest.mark.anyio
async def test_exact_two_request_confirmed_payment_sequence():
    """
    User Reproduction Sequence (Call ID 73 reproduction for Customer 1):
    Turn 1: Customer sends 'My salary is delayed. I may be able to pay on 28 October 2026.'
            API returns:
            - proposed_payment_date = 2026-10-28
            - needs_confirmation = true
            - commitment_status = NOT_CONFIRMED
            - call_status = INITIATED
            - Agent explicitly asks a date-specific confirmation question for 28 October 2026.
            - DB records pending_payment_date = 2026-10-28.
    Turn 2: Customer sends 'Yes, I confirm that I will pay on 28 October 2026.'
            API returns:
            - commitment_status = CONFIRMED
            - call_status = COMPLETED
            - DB records promised_payment_date = 2026-10-28, payment_commitment_confirmed = true,
              pending_payment_date = NULL, call_status = COMPLETED.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Start simulated call for customer 1
        call_res = await client.post("/customers/1/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # Turn 1
            reply_1 = "I understand your salary is delayed, Rahul. Would you be able to pay the INR 5,000 on 28 October 2026?"
            extraction_1 = CommitmentExtraction(
                reason="Salary is delayed.",
                expected_salary_date=None,
                proposed_payment_date=date(2026, 10, 28),
                intent="SALARY_DELAY",
                needs_confirmation=True,
            )

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_1)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=extraction_1)):
                res_1 = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "My salary is delayed. I may be able to pay on 28 October 2026."},
                )
                assert res_1.status_code == 200
                data_1 = res_1.json()
                assert data_1["call_status"] == "INITIATED"
                assert data_1["commitment_status"] == "NOT_CONFIRMED"
                assert data_1["extraction"]["proposed_payment_date"] == "2026-10-28"
                assert data_1["extraction"]["needs_confirmation"] is True
                assert "28 October 2026" in data_1["agent_reply"]

            # Verify intermediate DB state
            async with AsyncSessionLocal() as db:
                call_db = await db.get(Call, call_id)
                assert call_db is not None
                assert call_db.pending_payment_date == date(2026, 10, 28)
                assert call_db.promised_payment_date is None
                assert call_db.payment_commitment_confirmed is False
                assert call_db.call_status == "INITIATED"

            # Turn 2
            reply_2 = "Thank you, Rahul. I have confirmed your payment commitment of INR 5,000 for 28 October 2026. Have a great day!"
            extraction_2 = CommitmentExtraction(
                reason=None,
                expected_salary_date=None,
                proposed_payment_date=date(2026, 10, 28),
                intent="PROMISE_TO_PAY",
                needs_confirmation=False,
            )

            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_2)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=extraction_2)):
                res_2 = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "Yes, I confirm that I will pay on 28 October 2026."},
                )
                assert res_2.status_code == 200
                data_2 = res_2.json()
                assert data_2["commitment_status"] == "CONFIRMED"
                assert data_2["call_status"] == "COMPLETED"

            # Verify final DB state in fresh session
            async with AsyncSessionLocal() as db:
                final_call = await db.get(Call, call_id)
                assert final_call is not None
                assert final_call.payment_commitment_confirmed is True
                assert final_call.promised_payment_date == date(2026, 10, 28)
                assert final_call.pending_payment_date is None
                assert final_call.call_status == "COMPLETED"
                assert final_call.ended_at is not None
                assert "Payment commitment status: CONFIRMED" in (final_call.call_summary or "")
                assert "Promised payment date: 2026-10-28" in (final_call.call_summary or "")

        finally:
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await cleanup_db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await cleanup_db.commit()


@pytest.mark.anyio
async def test_affirmative_without_preceding_date_confirmation_question():
    """
    Requirement 3:
    If the previous agent message did not ask a date-specific confirmation question,
    a customer's affirmative must not confirm the commitment.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        call_res = await client.post("/customers/1/call")
        assert call_res.status_code == 201
        call_id = call_res.json()["call_id"]

        try:
            # Case A: Turn 1 immediate customer affirmative without any prior proposal
            reply_a = "Would you be able to pay the INR 5,000 on 28 October 2026?"
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_a)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                res_a = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": "Yes, I confirm that I will pay on 28 October 2026."},
                )
                assert res_a.status_code == 200
                data_a = res_a.json()
                # Must NOT confirm because there was no prior date confirmation question from agent!
                assert data_a["commitment_status"] == "NOT_CONFIRMED"
                assert data_a["call_status"] == "INITIATED"

            # In DB: commitment is NOT confirmed, but pending date is now set to 28 October 2026
            async with AsyncSessionLocal() as db:
                c_a = await db.get(Call, call_id)
                assert c_a.payment_commitment_confirmed is False
                assert c_a.promised_payment_date is None
                assert c_a.pending_payment_date == date(2026, 10, 28)

        finally:
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id})
                await cleanup_db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id})
                await cleanup_db.commit()


@pytest.mark.anyio
async def test_rejection_and_conflicting_date_cases_e2e():
    """
    Requirement 4:
    Reject ambiguous replies, explicit rejections, and conflicting dates.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Test Case 1: Explicit rejection
        call_res_1 = await client.post("/customers/1/call")
        call_id_1 = call_res_1.json()["call_id"]
        try:
            reply_q = "Would you be able to pay the INR 5,000 on 28 October 2026?"
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_q)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                await client.post(
                    f"/calls/{call_id_1}/message",
                    json={"message": "I may be able to pay on 28 October 2026."},
                )

            # Customer rejects
            reply_rej = "I understand. Thank you for letting us know."
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_rej)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                rej_res = await client.post(
                    f"/calls/{call_id_1}/message",
                    json={"message": "No, I cannot pay on that date."},
                )
                assert rej_res.status_code == 200
                data_rej = rej_res.json()
                assert data_rej["commitment_status"] == "DECLINED"
                assert data_rej["call_status"] == "INITIATED"

            async with AsyncSessionLocal() as db:
                c1 = await db.get(Call, call_id_1)
                assert c1.payment_commitment_confirmed is False
                assert c1.promised_payment_date is None
                assert c1.pending_payment_date is None
        finally:
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id_1})
                await cleanup_db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id_1})
                await cleanup_db.commit()

        # Test Case 2: Conflicting date
        call_res_2 = await client.post("/customers/1/call")
        call_id_2 = call_res_2.json()["call_id"]
        try:
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_q)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                await client.post(
                    f"/calls/{call_id_2}/message",
                    json={"message": "I may be able to pay on 28 October 2026."},
                )

            # Customer replies with conflicting date
            reply_conflict = "Would you be able to pay on 15 November 2026?"
            with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=reply_conflict)), \
                 patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=None)):
                conf_res = await client.post(
                    f"/calls/{call_id_2}/message",
                    json={"message": "No, I will pay on 15 November 2026 instead."},
                )
                assert conf_res.status_code == 200
                data_conf = conf_res.json()
                assert data_conf["commitment_status"] in {"NEEDS_CLARIFICATION", "NOT_CONFIRMED"}
                assert data_conf["call_status"] == "INITIATED"

            async with AsyncSessionLocal() as db:
                c2 = await db.get(Call, call_id_2)
                assert c2.payment_commitment_confirmed is False
                assert c2.promised_payment_date is None
        finally:
            async with AsyncSessionLocal() as cleanup_db:
                await cleanup_db.execute(text("DELETE FROM conversation_messages WHERE call_id = :cid"), {"cid": call_id_2})
                await cleanup_db.execute(text("DELETE FROM calls WHERE call_id = :cid"), {"cid": call_id_2})
                await cleanup_db.commit()


# ===========================================================================
# Runner
# ===========================================================================

async def run_async_tests():
    print("Running asynchronous e2e and persistence tests...")
    await test_end_to_end_bug_scenario_persistence()
    await test_subsequent_message_after_tentative_flow()
    await test_failure_rollback_no_partial_commitment()
    await test_exact_two_request_confirmed_payment_sequence()
    await test_affirmative_without_preceding_date_confirmation_question()
    await test_rejection_and_conflicting_date_cases_e2e()
    await close_database()
    print("Persistence and e2e tests passed.")


def run_all_tests():
    print("Running unit regression tests...")
    test_tentative_date_with_uncertainty_not_confirmed()
    test_ambiguous_response()
    test_explicit_affirmative_confirmation()
    test_explicit_rejection()
    test_conflicting_date_response()
    test_first_message_no_prior_proposal()
    test_agent_reply_with_due_date_not_mistaken()
    print("Unit tests passed.")

    asyncio.run(run_async_tests())
    print("\nALL 10 SCENARIO REGRESSION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_all_tests()
