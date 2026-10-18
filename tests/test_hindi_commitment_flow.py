import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.customers import CustomerCommitmentRequest, record_customer_commitment
from app.api.conversations import (
    extract_customer_proposed_date,
    extract_pending_payment_date,
    send_customer_message,
    CustomerMessageRequest,
    generate_call_summary,
)
from app.models import Call, ConversationMessage, Customer, EMI
from app.db.database import get_db
from app.main import app
from app.services.commitment_service import CommitmentExtraction
from app.services.confirmation_service import (
    ConfirmationResult,
    ConfirmationStatus,
    verify_payment_confirmation,
)


def create_mock_db(
    customer: Customer | None,
    call: Call | None,
    emi: EMI | None,
    agent_message: ConversationMessage | None = None,
    messages_list: list[ConversationMessage] | None = None,
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
            if "ORDER BY conversation_messages.message_id DESC" in stmt_str or "order_by" in stmt_str and "desc" in stmt_str:
                if agent_message is not None:
                    result.scalar_one_or_none.return_value = agent_message
                elif messages_list:
                    agent_msgs = [m for m in messages_list if m.speaker == "AGENT"]
                    result.scalar_one_or_none.return_value = agent_msgs[-1] if agent_msgs else None
                else:
                    result.scalar_one_or_none.return_value = None
            else:
                result.scalars.return_value.all.return_value = messages_list or []
        else:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
        return result

    db.execute.side_effect = execute_side_effect
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def get_hindi_fixtures():
    customer = Customer(
        customer_id=3,
        full_name="Arjun Kumar",
        phone_number="+919876543212",
        preferred_language="hi",
    )
    call = Call(
        call_id=188,
        customer_id=3,
        emi_id=8,
        call_status="INITIATED",
        pending_payment_date=None,
        promised_payment_date=None,
        payment_commitment_confirmed=False,
    )
    emi = EMI(
        emi_id=8,
        customer_id=3,
        emi_amount=4500.00,
        due_date=date(2026, 10, 12),
        status="OVERDUE",
    )
    return customer, call, emi


# ===========================================================================
# 1. Date Extraction Tests for Hindi Messages
# ===========================================================================

def test_hindi_date_extraction_customer_proposed():
    """Extract proposed date from Hindi customer messages."""
    # Case A: Explicit promise to pay
    msg1 = "मैं 25 अक्टूबर 2026 को EMI का भुगतान करने का वादा करता हूँ।"
    assert extract_customer_proposed_date(msg1) == date(2026, 10, 25)

    # Case B: Salary notification with payment mention
    msg2 = "मेरी सैलरी 25 अक्टूबर को आएगी, मैं उसके बाद भुगतान कर दूँगा।"
    assert extract_customer_proposed_date(msg2) == date(date.today().year, 10, 25)

    # Case C: Pure salary delay without payment date
    msg3 = "मेरी सैलरी अभी नहीं आई है।"
    assert extract_customer_proposed_date(msg3) is None


def test_hindi_date_extraction_pending_agent_question():
    """Extract pending payment date from Hindi agent confirmation questions."""
    # Valid date-specific question
    q1 = "क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि कर सकते हैं?"
    assert extract_pending_payment_date(q1) == date(2026, 10, 25)

    # Valid shorter question
    q2 = "क्या आप 25 अक्टूबर 2026 को भुगतान कर सकते हैं?"
    assert extract_pending_payment_date(q2) == date(2026, 10, 25)

    # Natural Hindi wording emitted by the agent must also establish the
    # exact pending proposal required for the customer's next-turn confirmation.
    q3 = "क्या आप 25 अक्टूबर 2026 को भुगतान करने का वादा कर सकते हैं?"
    assert extract_pending_payment_date(q3) == date(2026, 10, 25)

    # The configured LLM may use chandrabindu in the natural Hindi modal verb.
    q4 = "क्या आप 25 अक्टूबर 2026 को ₹6,200 की EMI का भुगतान कर पाएँगे?"
    assert extract_pending_payment_date(q4) == date(2026, 10, 25)

    # Non-payment inquiry
    q5 = "आपकी सैलरी किस तारीख को आएगी?"
    assert extract_pending_payment_date(q5) is None

    # Not a question
    statement = "आपकी EMI 25 अक्टूबर 2026 को देय है।"
    assert extract_pending_payment_date(statement) is None


# ===========================================================================
# 2. Confirmation Service Tests for Hindi Messages
# ===========================================================================

def test_hindi_confirmation_service_affirmation():
    """Verify that explicit Hindi affirmations confirm the pending date."""
    pending = date(2026, 10, 25)
    agent_q = "क्या आप 25 अक्टूबर 2026 को EMI का भुगतान करने की पुष्टि कर सकते हैं?"

    affirmative_responses = (
        "हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।",
        "हाँ, मैं 25 अक्टूबर 2026 को भुगतान करूँगा।",
        "हाँ",
        "हाँ मैं भुगतान करूँगा",
        "हाँ मैं पुष्टि करता हूँ",
        "जी हाँ, मैं 25 अक्टूबर 2026 को भुगतान कर दूँगा।",
        "मैं 25 अक्टूबर 2026 को भुगतान करने की पुष्टि करता हूँ।",
        "बिल्कुल",
        "ज़रूर",
    )

    for resp in affirmative_responses:
        res = verify_payment_confirmation(
            customer_response=resp,
            proposed_payment_date=pending,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert res.status == ConfirmationStatus.CONFIRMED, f"Failed for: {resp}"
        assert res.payment_date == pending


def test_confirmation_requires_matching_amount_in_proposal_and_rejects_conflicting_amount():
    pending = date(2026, 10, 25)
    response = "हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।"
    proposal = "क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान कर सकते हैं?"

    valid = verify_payment_confirmation(
        customer_response=response,
        proposed_payment_date=pending,
        agent_last_message=proposal,
        pending_proposal_date=pending,
        proposed_payment_amount=4500,
    )
    assert valid.status == ConfirmationStatus.CONFIRMED

    wrong_proposal_amount = verify_payment_confirmation(
        customer_response=response,
        proposed_payment_date=pending,
        agent_last_message=proposal.replace("4500", "5000"),
        pending_proposal_date=pending,
        proposed_payment_amount=4500,
    )
    assert wrong_proposal_amount.status == ConfirmationStatus.NO_PENDING_PROPOSAL

    conflicting_response_amount = verify_payment_confirmation(
        customer_response=response.replace("4500", "5000"),
        proposed_payment_date=pending,
        agent_last_message=proposal,
        pending_proposal_date=pending,
        proposed_payment_amount=4500,
    )
    assert conflicting_response_amount.status == ConfirmationStatus.AMBIGUOUS


def test_hindi_confirmation_service_rejection():
    """Verify that Hindi rejections are marked as NOT_CONFIRMED."""
    pending = date(2026, 10, 25)
    agent_q = "क्या आप 25 अक्टूबर 2026 को EMI का भुगतान करने की पुष्टि कर सकते हैं?"

    for resp in (
        "नहीं, मैं उस तारीख को भुगतान नहीं करूँगा।",
        "नहीं",
        "मैं भुगतान नहीं कर सकता",
        "उस तारीख को नहीं",
        "मैं उस तारीख को भुगतान करने से मना करता हूँ।",
    ):
        res = verify_payment_confirmation(
            customer_response=resp,
            proposed_payment_date=pending,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert res.status == ConfirmationStatus.NOT_CONFIRMED, f"Failed for: {resp}"


def test_hindi_confirmation_service_ambiguity():
    """Verify that tentative/ambiguous Hindi replies are marked as AMBIGUOUS."""
    pending = date(2026, 10, 25)
    agent_q = "क्या आप 25 अक्टूबर 2026 को EMI का भुगतान करने की पुष्टि कर सकते हैं?"

    for resp in (
        "शायद, मैं कोशिश करूँगा।",
        "पक्का नहीं है",
        "सैलरी के बाद देखूँगा",
        "गारंटी नहीं दे सकता",
    ):
        res = verify_payment_confirmation(
            customer_response=resp,
            proposed_payment_date=pending,
            agent_last_message=agent_q,
            pending_proposal_date=pending,
        )
        assert res.status == ConfirmationStatus.AMBIGUOUS, f"Failed for: {resp}"


def test_hindi_confirmation_service_conflicting_date():
    """Verify that conflicting dates in Hindi response are rejected."""
    pending = date(2026, 10, 25)
    agent_q = "क्या आप 25 अक्टूबर 2026 को EMI का भुगतान करने की पुष्टि कर सकते हैं?"

    res = verify_payment_confirmation(
        customer_response="नहीं, मैं 30 अक्टूबर 2026 को भुगतान करूँगा।",
        proposed_payment_date=pending,
        agent_last_message=agent_q,
        pending_proposal_date=pending,
    )
    assert res.status == ConfirmationStatus.AMBIGUOUS
    assert res.payment_date is None


# ===========================================================================
# 3. Commitment API Endpoint Tests for Hindi Flow
# ===========================================================================

@pytest.mark.anyio
async def test_commitment_endpoint_confirmed_with_hindi_confirmation_text():
    """POST /customers/{id}/commitment confirms commitment when pending date and agent question exist."""
    customer, call, emi = get_hindi_fixtures()
    call.pending_payment_date = date(2026, 10, 25)

    agent_msg = ConversationMessage(
        message_id=50,
        call_id=188,
        speaker="AGENT",
        message_text="क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि कर सकते हैं?",
    )

    db = create_mock_db(customer, call, emi, agent_message=agent_msg)
    request = CustomerCommitmentRequest(
        call_id=188,
        promised_payment_date=date(2026, 10, 25),
        reason="Salary has not arrived yet",
        expected_salary_date=date(2026, 10, 25),
        is_confirmed=True,
        confirmation_text="हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।",
    )

    response = await record_customer_commitment(customer_id=3, request=request, db=db)

    assert response.payment_commitment_confirmed is True
    assert response.promised_payment_date == date(2026, 10, 25)
    assert response.pending_payment_date is None
    assert response.call_status == "COMPLETED"

    # Verify DB mutations
    assert call.payment_commitment_confirmed is True
    assert call.promised_payment_date == date(2026, 10, 25)
    assert call.pending_payment_date is None
    assert call.call_status == "COMPLETED"
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_commitment_endpoint_rejected_without_pending_proposal():
    """POST /customers/{id}/commitment rejects request when no pending date proposal exists."""
    customer, call, emi = get_hindi_fixtures()
    call.pending_payment_date = None  # No pending proposal

    agent_msg = ConversationMessage(
        message_id=50,
        call_id=188,
        speaker="AGENT",
        message_text="नमस्ते अर्जुन कुमार जी।",
    )

    db = create_mock_db(customer, call, emi, agent_message=agent_msg)
    request = CustomerCommitmentRequest(
        call_id=188,
        promised_payment_date=date(2026, 10, 25),
        is_confirmed=True,
        confirmation_text="हाँ, मैं पुष्टि करता हूँ।",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(customer_id=3, request=request, db=db)

    assert exc_info.value.status_code == 400
    assert "pending payment date proposal" in exc_info.value.detail.lower()
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_commitment_endpoint_rejects_conflicting_date_and_confirmation():
    customer, call, emi = get_hindi_fixtures()
    call.pending_payment_date = date(2026, 10, 25)
    agent_msg = ConversationMessage(
        message_id=50,
        call_id=call.call_id,
        speaker="AGENT",
        message_text="क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान कर सकते हैं?",
    )

    conflicting_db = create_mock_db(customer, call, emi, agent_message=agent_msg)
    with pytest.raises(HTTPException) as conflict_exc:
        await record_customer_commitment(
            customer_id=customer.customer_id,
            request=CustomerCommitmentRequest(
                call_id=call.call_id,
                promised_payment_date=date(2026, 10, 30),
                is_confirmed=True,
                confirmation_text="हाँ, मैं 30 अक्टूबर 2026 को भुगतान करूँगा।",
            ),
            db=conflicting_db,
        )
    assert conflict_exc.value.status_code == 400
    assert "does not match" in conflict_exc.value.detail.lower()
    conflicting_db.rollback.assert_awaited_once()

    invalid_db = create_mock_db(customer, call, emi, agent_message=agent_msg)
    with pytest.raises(HTTPException) as invalid_exc:
        await record_customer_commitment(
            customer_id=customer.customer_id,
            request=CustomerCommitmentRequest(
                call_id=call.call_id,
                promised_payment_date=date(2026, 10, 25),
                is_confirmed=True,
                confirmation_text="शायद, मैं कोशिश करूँगा।",
            ),
            db=invalid_db,
        )
    assert invalid_exc.value.status_code == 400
    assert "does not confirm" in invalid_exc.value.detail.lower()
    invalid_db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_commitment_endpoint_idempotent_retry():
    """POST /customers/{id}/commitment returns confirmed status on idempotent retries."""
    customer, call, emi = get_hindi_fixtures()
    call.payment_commitment_confirmed = True
    call.promised_payment_date = date(2026, 10, 25)
    call.pending_payment_date = None
    call.call_status = "COMPLETED"

    db = create_mock_db(customer, call, emi)
    request = CustomerCommitmentRequest(
        call_id=188,
        promised_payment_date=date(2026, 10, 25),
        reason="Updated salary note",
        expected_salary_date=date(2026, 10, 25),
        is_confirmed=True,
        confirmation_text="हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।",
    )

    response = await record_customer_commitment(customer_id=3, request=request, db=db)

    assert response.payment_commitment_confirmed is True
    assert response.promised_payment_date == date(2026, 10, 25)
    assert response.pending_payment_date is None
    assert call.reason == "Updated salary note"
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_commitment_endpoint_does_not_report_success_when_commit_fails():
    customer, call, emi = get_hindi_fixtures()
    pending = date(2026, 10, 25)
    call.pending_payment_date = pending
    agent_message = ConversationMessage(
        call_id=call.call_id,
        speaker="AGENT",
        message_text="क्या आप 25 अक्टूबर 2026 को INR 4500 की EMI का भुगतान करने की पुष्टि करते हैं?",
    )
    db = create_mock_db(customer, call, emi, agent_message=agent_message)
    db.commit.side_effect = SQLAlchemyError("synthetic commit failure")

    with pytest.raises(HTTPException) as failure:
        await record_customer_commitment(
            customer_id=customer.customer_id,
            request=CustomerCommitmentRequest(
                call_id=call.call_id,
                promised_payment_date=pending,
                is_confirmed=True,
                confirmation_text=(
                    "हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।"
                ),
            ),
            db=db,
        )

    assert failure.value.status_code == 503
    assert "temporarily unavailable" in failure.value.detail
    db.rollback.assert_awaited_once()


# ===========================================================================
# 4. End-to-End Turn Sequence Simulation
# ===========================================================================

@pytest.mark.anyio
async def test_e2e_hindi_conversation_turn_sequence():
    """
    Simulate the complete Turn C -> Turn D conversation lifecycle:
    Turn C: Customer promises date -> Agent asks confirmation in Hindi -> pending_payment_date persisted.
    Turn D: Customer affirms in Hindi -> commitment confirmed, promised_payment_date set, pending cleared.
    """
    customer, call, emi = get_hindi_fixtures()

    # --- Turn C: Customer proposes payment date ---
    turn_c_cust_msg = (
        "मेरी सैलरी अभी तक नहीं आई है, मैं 25 अक्टूबर 2026 को EMI का "
        "भुगतान करने का वादा करता हूँ।"
    )
    turn_c_agent_reply = "क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने का वादा कर सकते हैं?"

    msg_history_c = [
        ConversationMessage(message_id=1, call_id=188, speaker="CUSTOMER", message_text=turn_c_cust_msg)
    ]
    db_c = create_mock_db(customer, call, emi, messages_list=msg_history_c)

    with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=turn_c_agent_reply)), \
         patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=CommitmentExtraction(
             reason=None,
             expected_salary_date=None,
             proposed_payment_date=date(2026, 10, 25),
             intent="PROMISE_TO_PAY",
             needs_confirmation=True,
         ))):
        res_c = await send_customer_message(
            call_id=188,
            request=CustomerMessageRequest(message=turn_c_cust_msg),
            db=db_c,
        )

    # Turn C assertions:
    assert res_c.commitment_status == "NOT_CONFIRMED"
    assert call.payment_commitment_confirmed is False
    # Pending date proposal MUST be persisted in DB!
    assert call.pending_payment_date == date(2026, 10, 25)

    # --- Turn D: Customer confirms payment date ---
    turn_d_cust_msg = "हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।"
    turn_d_agent_reply = "धन्यवाद अर्जुन जी, आपकी 25 अक्टूबर 2026 की EMI भुगतान की पुष्टि दर्ज कर ली गई है।"

    # The DB now has:
    # - call.pending_payment_date = date(2026, 10, 25)
    # - previous agent message = turn_c_agent_reply
    msg_history_d = [
        ConversationMessage(message_id=1, call_id=188, speaker="CUSTOMER", message_text=turn_c_cust_msg),
        ConversationMessage(message_id=2, call_id=188, speaker="AGENT", message_text=turn_c_agent_reply),
        ConversationMessage(message_id=3, call_id=188, speaker="CUSTOMER", message_text=turn_d_cust_msg),
    ]
    db_d = create_mock_db(customer, call, emi, messages_list=msg_history_d)

    with patch("app.api.conversations.generate_agent_reply", new=AsyncMock(return_value=turn_d_agent_reply)), \
         patch("app.api.conversations.extract_commitment_details", new=AsyncMock(return_value=CommitmentExtraction(
             reason=None,
             expected_salary_date=None,
             proposed_payment_date=date(2026, 10, 25),
             intent="PROMISE_TO_PAY",
             needs_confirmation=False,
         ))):
        res_d = await send_customer_message(
            call_id=188,
            request=CustomerMessageRequest(message=turn_d_cust_msg),
            db=db_d,
        )

    # Turn D assertions:
    assert res_d.commitment_status == "CONFIRMED"
    assert res_d.call_status == "COMPLETED"
    assert call.payment_commitment_confirmed is True
    assert call.promised_payment_date == date(2026, 10, 25)
    assert call.pending_payment_date is None
    assert call.call_status == "COMPLETED"
    assert "Payment commitment status: CONFIRMED" in call.call_summary
    assert "Promised payment date: 2026-10-25" in call.call_summary

    # The separate commitment endpoint accepts an idempotent retry after the
    # conversation handler has confirmed the proposal and cleared its pending
    # date. It must not demand a proposal that has already been consumed.
    commitment_db = create_mock_db(customer, call, emi)
    commitment_response = await record_customer_commitment(
        customer_id=customer.customer_id,
        request=CustomerCommitmentRequest(
            call_id=call.call_id,
            promised_payment_date=date(2026, 10, 25),
            is_confirmed=True,
            confirmation_text=turn_d_cust_msg,
        ),
        db=commitment_db,
    )
    assert commitment_response.payment_commitment_confirmed is True
    assert commitment_response.promised_payment_date == date(2026, 10, 25)
    commitment_db.commit.assert_awaited_once()

    summary_db = create_mock_db(customer, call, emi)
    summary = await generate_call_summary(call_id=call.call_id, db=summary_db)
    assert summary.payment_commitment_confirmed is True
    assert summary.promised_payment_date == date(2026, 10, 25)
    assert "Promised payment date: 2026-10-25" in summary.call_summary


@pytest.mark.anyio
async def test_commitment_endpoint_rejects_call_owned_by_another_customer():
    customer, call, emi = get_hindi_fixtures()
    call.pending_payment_date = date(2026, 10, 25)
    db = create_mock_db(customer, call, emi)

    request = CustomerCommitmentRequest(
        call_id=call.call_id,
        promised_payment_date=date(2026, 10, 25),
        is_confirmed=True,
        confirmation_text="हाँ, मैं भुगतान करूँगा।",
    )

    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(
            customer_id=customer.customer_id + 1,
            request=request,
            db=db,
        )

    assert exc_info.value.status_code == 409
    db.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_rejection_clears_proposal_and_commitment_cannot_reuse_it():
    customer, call, emi = get_hindi_fixtures()
    pending = date(2026, 10, 25)
    call.pending_payment_date = pending
    agent_question = ConversationMessage(
        message_id=10,
        call_id=call.call_id,
        speaker="AGENT",
        message_text="क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान कर सकते हैं?",
    )
    history = [
        ConversationMessage(
            message_id=9,
            call_id=call.call_id,
            speaker="CUSTOMER",
            message_text="मैं 25 अक्टूबर 2026 को भुगतान करूँगा।",
        ),
        agent_question,
    ]
    conversation_db = create_mock_db(
        customer,
        call,
        emi,
        agent_message=agent_question,
        messages_list=history,
    )

    with patch(
        "app.api.conversations.generate_agent_reply",
        new=AsyncMock(return_value="मैं समझता हूँ। क्या आप कोई दूसरी तारीख बता सकते हैं?"),
    ), patch(
        "app.api.conversations.extract_commitment_details",
        new=AsyncMock(return_value=None),
    ):
        response = await send_customer_message(
            call_id=call.call_id,
            request=CustomerMessageRequest(message="नहीं, मैं उस तारीख को भुगतान नहीं करूँगा।"),
            db=conversation_db,
        )

    assert response.commitment_status == "DECLINED"
    assert call.payment_commitment_confirmed is False
    assert call.pending_payment_date is None

    commitment_db = create_mock_db(customer, call, emi)
    with pytest.raises(HTTPException) as exc_info:
        await record_customer_commitment(
            customer_id=customer.customer_id,
            request=CustomerCommitmentRequest(
                call_id=call.call_id,
                promised_payment_date=pending,
                is_confirmed=True,
                confirmation_text="हाँ, मैं भुगतान करूँगा।",
            ),
            db=commitment_db,
        )

    assert exc_info.value.status_code == 400
    assert "pending payment date proposal" in exc_info.value.detail.lower()
    commitment_db.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("proposal_message", "first_reply", "confirmation_text"),
    [
        (
            "My salary has not arrived yet. I promise to pay the EMI on 25 October 2026.",
            "Can you confirm that you will pay INR {amount} on 25 October 2026?",
            "Yes, I confirm that I will pay INR {amount} on 25 October 2026.",
        ),
        (
            "मेरी सैलरी अभी तक नहीं आई है। मैं 25 अक्टूबर 2026 को EMI का भुगतान करने का वादा करता हूँ।",
            "क्या आप 25 अक्टूबर 2026 को INR {amount} की EMI का भुगतान करने का वादा कर सकते हैं?",
            "हाँ, मैं 25 अक्टूबर 2026 को ₹{amount} की EMI का भुगतान करने की पुष्टि करता हूँ।",
        ),
    ],
    ids=("english", "hindi"),
)
@pytest.mark.parametrize(
    ("customer_id", "customer_name", "emi_amount"),
    [(2, "Priya Reddy", 6200.0), (3, "Arjun Kumar", 4500.0)],
    ids=("customer-2-emi-6200", "customer-3-emi-4500"),
)
async def test_http_english_and_hindi_flow_and_documented_health_routes(
    proposal_message: str,
    first_reply: str,
    confirmation_text: str,
    customer_id: int,
    customer_name: str,
    emi_amount: float,
):
    """Exercise both customer mappings and languages with isolated state."""
    customer = Customer(
        customer_id=customer_id,
        full_name=customer_name,
        phone_number="+919876543212",
        preferred_language=("hi" if confirmation_text.startswith("हाँ") else "en"),
    )
    emi = EMI(
        emi_id=customer_id,
        customer_id=customer.customer_id,
        emi_amount=emi_amount,
        due_date=date(2026, 10, 25),
        status="PENDING",
    )
    state = {"customer": customer, "call": None, "emi": emi, "messages": []}
    next_ids = {"call": 1, "message": 1}

    class IsolatedSession:
        async def execute(self, statement):
            query = str(statement)
            result = MagicMock()
            if "FROM customers" in query:
                result.scalar_one_or_none.return_value = state["customer"]
            elif "FROM calls" in query:
                result.scalar_one_or_none.return_value = state["call"]
            elif "FROM emis" in query:
                if "ORDER BY emis.due_date ASC" in query and "LIMIT" not in query:
                    result.scalars.return_value.all.return_value = [state["emi"]]
                else:
                    result.scalar_one_or_none.return_value = state["emi"]
            elif "FROM conversation_messages" in query:
                if "ORDER BY conversation_messages.message_id DESC" in query:
                    agent_messages = [
                        message for message in state["messages"]
                        if message.speaker == "AGENT"
                    ]
                    result.scalar_one_or_none.return_value = (
                        agent_messages[-1] if agent_messages else None
                    )
                else:
                    result.scalars.return_value.all.return_value = list(state["messages"])
            else:
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        def add(self, record):
            if isinstance(record, Call):
                record.call_id = next_ids["call"]
                next_ids["call"] += 1
                state["call"] = record
            elif isinstance(record, ConversationMessage):
                record.message_id = next_ids["message"]
                next_ids["message"] += 1
                state["messages"].append(record)

        async def flush(self):
            return None

        async def refresh(self, record, attribute_names=None):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    isolated_session = IsolatedSession()

    async def override_get_db():
        yield isolated_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/health")
            assert health.status_code == 200

            with patch(
                "app.main.check_database_connection",
                new=AsyncMock(return_value={"database": "isolated-test", "status": "connected"}),
            ):
                db_health = await client.get("/health/database")
            assert db_health.status_code == 200

            emi_response = await client.get(f"/customers/{customer.customer_id}/emi")
            assert emi_response.status_code == 200
            assert emi_response.json()["customer_id"] == customer.customer_id
            assert emi_response.json()["emis"][0]["emi_amount"] == emi_amount

            start_response = await client.post(f"/customers/{customer.customer_id}/call")
            assert start_response.status_code == 201
            call_id = start_response.json()["call_id"]
            assert start_response.json()["customer_id"] == customer.customer_id
            assert start_response.json()["emi_id"] == emi.emi_id
            assert start_response.json()["emi_amount"] == float(emi.emi_amount)
            # Check the persisted call object as well as its API response: the
            # route's customer ID must survive call creation unchanged.
            assert state["call"].customer_id == customer.customer_id
            assert state["call"].emi_id == emi.emi_id

            with patch(
                "app.api.conversations.generate_agent_reply",
                new=AsyncMock(return_value=first_reply.format(amount=f"{emi_amount:g}")),
            ), patch(
                "app.api.conversations.extract_commitment_details",
                new=AsyncMock(return_value=CommitmentExtraction(
                    reason="Salary has not arrived",
                    expected_salary_date=None,
                    proposed_payment_date=date(2026, 10, 25),
                    intent="PROMISE_TO_PAY",
                    needs_confirmation=True,
                )),
            ):
                proposal_response = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": proposal_message},
                )
            assert proposal_response.status_code == 200
            assert proposal_response.json()["commitment_status"] == "NOT_CONFIRMED"
            assert proposal_response.json()["customer_id"] == customer.customer_id
            assert proposal_response.json()["emi_id"] == emi.emi_id
            assert proposal_response.json()["emi_amount"] == emi_amount
            assert f"{emi_amount:g}" in proposal_response.json()["agent_reply"]
            if customer_id == 2:
                assert "4500" not in proposal_response.json()["agent_reply"]
            assert state["call"].pending_payment_date == date(2026, 10, 25)

            with patch(
                "app.api.conversations.generate_agent_reply",
                new=AsyncMock(return_value="धन्यवाद, आपकी पुष्टि दर्ज की गई है।"),
            ), patch(
                "app.api.conversations.extract_commitment_details",
                new=AsyncMock(return_value=None),
            ):
                confirmation_response = await client.post(
                    f"/calls/{call_id}/message",
                    json={"message": confirmation_text.format(amount=f"{emi_amount:g}")},
                )
            assert confirmation_response.status_code == 200
            assert confirmation_response.json()["commitment_status"] == "CONFIRMED"
            assert confirmation_response.json()["call_status"] == "COMPLETED"
            assert confirmation_response.json()["customer_id"] == customer_id
            assert confirmation_response.json()["emi_id"] == emi.emi_id
            assert confirmation_response.json()["emi_amount"] == emi_amount
            assert state["call"].pending_payment_date is None
            assert state["call"].promised_payment_date == date(2026, 10, 25)

            commitment_response = await client.post(
                f"/customers/{customer.customer_id}/commitment",
                json={
                    "call_id": call_id,
                    "promised_payment_date": "2026-10-25",
                    "is_confirmed": True,
                    "confirmation_text": confirmation_text.format(amount=f"{emi_amount:g}"),
                },
            )
            assert commitment_response.status_code == 200
            assert commitment_response.json()["payment_commitment_confirmed"] is True
            assert commitment_response.json()["emi_amount"] == emi_amount

            summary_response = await client.post(f"/calls/{call_id}/summary")
            assert summary_response.status_code == 200
            assert summary_response.json()["customer_id"] == customer_id
            assert summary_response.json()["emi_id"] == emi.emi_id
            assert summary_response.json()["payment_commitment_confirmed"] is True
            assert "Promised payment date: 2026-10-25" in summary_response.json()["call_summary"]
            assert f"EMI amount: INR {emi_amount:.2f}" in summary_response.json()["call_summary"]
    finally:
        app.dependency_overrides.pop(get_db, None)
