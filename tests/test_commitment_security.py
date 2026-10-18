from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.customers import CustomerCommitmentRequest, record_customer_commitment
from app.models import Call, ConversationMessage, Customer, EMI


def create_mock_db(
    customer: Customer | None,
    call: Call | None,
    emi: EMI | None,
    agent_message: ConversationMessage | None = None,
):
    db = AsyncMock()

    async def execute_side_effect(stmt):
        stmt_str = str(stmt)
        result = MagicMock()
        if "FROM customers" in stmt_str:
            result.scalar_one_or_none.return_value = customer
        elif "FROM calls" in stmt_str:
            result.scalar_one_or_none.return_value = call
        elif "FROM emis" in stmt_str:
            result.scalar_one_or_none.return_value = emi
        elif "FROM conversation_messages" in stmt_str:
            result.scalar_one_or_none.return_value = agent_message
        else:
            result.scalar_one_or_none.return_value = None
        return result

    db.execute.side_effect = execute_side_effect
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def get_default_fixtures():
    customer = Customer(
        customer_id=1,
        full_name="Rahul Sharma",
        phone_number="+919876543210",
        preferred_language="en",
    )
    call = Call(
        call_id=10,
        customer_id=1,
        emi_id=5,
        call_status="INITIATED",
        pending_payment_date=date(2026, 11, 15),
        promised_payment_date=None,
        payment_commitment_confirmed=False,
    )
    emi = EMI(
        emi_id=5,
        customer_id=1,
        emi_amount=5000.00,
        due_date=date(2026, 11, 10),
        status="OVERDUE",
    )
    agent_msg = ConversationMessage(
        message_id=100,
        call_id=10,
        speaker="AGENT",
        message_text="Would you be able to pay the INR 5,000 on 15 November 2026?",
    )
    return customer, call, emi, agent_msg


@pytest.mark.anyio
async def test_confirmation_without_pending_payment_date_rejected():
    """1. Confirmation without a pending payment date is rejected."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = None  # No pending date exists

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        is_confirmed=True,
        confirmation_text="Yes, I confirm I will pay on 15 November 2026.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "pending payment date proposal" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_requested_date_different_from_pending_date_rejected():
    """2. A requested date different from the pending date is rejected."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 20),  # Mismatched date
        is_confirmed=True,
        confirmation_text="Yes, I confirm I will pay on 20 November 2026.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "does not match the pending proposal date" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_confirmation_rejected_when_no_stored_agent_message_exists():
    """3. Confirmation is rejected when no stored agent message exists."""
    customer, call, emi, _ = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)

    db = create_mock_db(customer, call, emi, agent_message=None)  # No agent message in DB
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        is_confirmed=True,
        confirmation_text="Yes, I confirm I will pay on 15 November 2026.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "no stored agent confirmation question found" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_confirmation_rejected_if_agent_message_does_not_ask_for_pending_date():
    """4. Confirmation is rejected if the latest agent message does not ask for confirmation of the pending date."""
    customer, call, emi, _ = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)

    # Agent asked a generic inquiry, not a date-specific confirmation question for 15 Nov 2026
    unrelated_agent_msg = ConversationMessage(
        message_id=100,
        call_id=10,
        speaker="AGENT",
        message_text="When do you expect your salary to arrive?",
    )
    db = create_mock_db(customer, call, emi, agent_message=unrelated_agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        is_confirmed=True,
        confirmation_text="Yes, I confirm I will pay on 15 November 2026.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "does not confirm the proposed date" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_explicit_affirmative_response_to_correct_stored_question_accepted():
    """5. An explicit affirmative response to the correct stored date-specific question is accepted."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        reason="Salary credited on 14th",
        expected_salary_date=date(2026, 11, 14),
        is_confirmed=True,
        confirmation_text="Yes, I confirm that I will pay on 15 November 2026.",
    )

    response = await record_customer_commitment(customer_id=1, request=request, db=db)

    assert response.payment_commitment_confirmed is True
    assert response.promised_payment_date == date(2026, 11, 15)
    assert response.pending_payment_date is None
    assert response.call_status == "COMPLETED"

    # Verify model state mutations
    assert call.payment_commitment_confirmed is True
    assert call.promised_payment_date == date(2026, 11, 15)
    assert call.pending_payment_date is None
    assert call.call_status == "COMPLETED"
    assert call.ended_at is not None
    assert call.reason == "Salary credited on 14th"
    assert call.expected_salary_date == date(2026, 11, 14)

    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_ambiguous_response_such_as_maybe_rejected():
    """6. An ambiguous response such as 'Maybe' is not accepted."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        is_confirmed=True,
        confirmation_text="Maybe, I will try to pay on that date.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "does not confirm the proposed date" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_conflicting_payment_date_rejected():
    """7. A conflicting payment date in the confirmation text is not accepted."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        is_confirmed=True,
        confirmation_text="No, I will pay on 25 November 2026 instead.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "does not confirm the proposed date" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_commitment_cannot_be_confirmed_for_another_customers_call():
    """8. A commitment cannot be confirmed for another customer's call."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.customer_id = 99  # Belongs to customer 99, but request is for customer 1

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 15),
        is_confirmed=True,
        confirmation_text="Yes, I confirm I will pay on 15 November 2026.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 409
    assert "call does not belong to this customer" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_unconfirmed_request_attempting_to_set_new_payment_date_rejected():
    """9. An unconfirmed request attempting to set a new payment date is rejected with HTTP 409."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = None
    call.payment_commitment_confirmed = False

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 20),
        reason="Awaiting bonus",
        is_confirmed=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 409
    assert "cannot be created or replaced" in exc_info.value.detail.lower()
    assert call.pending_payment_date is None
    assert call.payment_commitment_confirmed is False
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_unconfirmed_request_attempting_to_replace_existing_pending_date_rejected():
    """10. An unconfirmed request attempting to replace an existing pending date is rejected with HTTP 409."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)  # Existing pending date
    call.payment_commitment_confirmed = False

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 20),  # Attempting to replace with a different date
        is_confirmed=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 409
    assert "cannot be created or replaced" in exc_info.value.detail.lower()
    # Ensure existing pending date was NOT modified or replaced
    assert call.pending_payment_date == date(2026, 11, 15)
    assert call.payment_commitment_confirmed is False
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_stale_stored_agent_question_for_date_a_cannot_authorize_date_b():
    """11. A stale stored agent question for Date A cannot authorize confirmation of Date B."""
    customer, call, emi, _ = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 20)  # Date B
    call.payment_commitment_confirmed = False

    # Stored agent message asks for Date A (15 Nov 2026), but pending date is Date B (20 Nov 2026)
    stale_agent_msg = ConversationMessage(
        message_id=100,
        call_id=10,
        speaker="AGENT",
        message_text="Would you be able to pay the INR 5,000 on 15 November 2026?",
    )
    db = create_mock_db(customer, call, emi, agent_message=stale_agent_msg)

    # Customer claims confirmation for Date B (20 Nov 2026)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=date(2026, 11, 20),
        is_confirmed=True,
        confirmation_text="Yes, I confirm I will pay on 20 November 2026.",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "does not confirm the proposed date" in exc_info.value.detail.lower()
    assert call.payment_commitment_confirmed is False
    assert call.promised_payment_date is None
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_cannot_overwrite_already_confirmed_commitment_with_unconfirmed():
    """12. An existing confirmed commitment cannot be reset by an unconfirmed request."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.payment_commitment_confirmed = True
    call.promised_payment_date = date(2026, 11, 15)
    call.pending_payment_date = None

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=None,
        reason="Update reason after confirmation",
        is_confirmed=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=1, request=request, db=db)

    assert exc_info.value.status_code == 409
    assert "already been confirmed" in exc_info.value.detail.lower()
    assert call.payment_commitment_confirmed is True
    assert call.promised_payment_date == date(2026, 11, 15)
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_recording_permitted_non_date_details_does_not_change_existing_pending_proposal():
    """13. Recording permitted non-date details does not change an existing pending proposal."""
    customer, call, emi, agent_msg = get_default_fixtures()
    call.pending_payment_date = date(2026, 11, 15)  # Existing pending proposal from conversation
    call.payment_commitment_confirmed = False
    call.reason = None
    call.expected_salary_date = None

    db = create_mock_db(customer, call, emi, agent_msg)
    request = CustomerCommitmentRequest(
        call_id=10,
        promised_payment_date=None,  # No date proposal supplied
        reason="Salary delayed by accounts department",
        expected_salary_date=date(2026, 11, 14),
        is_confirmed=False,
    )

    response = await record_customer_commitment(customer_id=1, request=request, db=db)

    # Status must remain unconfirmed
    assert response.payment_commitment_confirmed is False
    assert response.promised_payment_date is None
    # Existing pending proposal must be strictly preserved
    assert response.pending_payment_date == date(2026, 11, 15)
    assert call.pending_payment_date == date(2026, 11, 15)
    assert call.payment_commitment_confirmed is False
    assert call.promised_payment_date is None
    # Non-date details must be saved
    assert call.reason == "Salary delayed by accounts department"
    assert call.expected_salary_date == date(2026, 11, 14)

    db.commit.assert_awaited_once()
