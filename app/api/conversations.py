import logging
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models import Call, Customer, EMI, ConversationMessage
from app.services.ai_service import generate_agent_reply
from app.services.commitment_service import (
    CommitmentExtraction,
    extract_commitment_details,
)
from app.services.confirmation_service import (
    CONFIRMATION_QUESTION_PHRASES,
    ConfirmationStatus,
    DEVANAGARI_DIGITS,
    extract_dates_from_text,
    verify_payment_confirmation,
)
from app.services.sentiment_service import assess_sentiment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["Conversations"])


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------



def extract_pending_payment_date(message: str) -> date | None:
    """Extract the payment date from the agent's confirmation question."""
    if not message or "?" not in message:
        return None

    raw_sentences = re.split(r"(?<=[.!?\n;।])\s+", message.strip())

    for sent in reversed(raw_sentences):
        sent = sent.strip()
        if not sent.endswith("?"):
            continue

        sent_lower = sent.lower().translate(DEVANAGARI_DIGITS)
        matched_phrase = next(
            (p for p in CONFIRMATION_QUESTION_PHRASES if p in sent_lower),
            None,
        )
        if not matched_phrase:
            continue

        if matched_phrase in ("confirm", "पुष्टि") and not any(
            k in sent_lower for k in ("pay", "payment", "date", "भुगतान", "तारीख", "तारीख़")
        ):
            continue

        candidates = []
        raw_candidates = extract_dates_from_text(sent)
        for pos, parsed, has_year in raw_candidates:
            if not has_year:
                # A date without a year is accepted only with explicit confirmation wording
                if not (
                    "confirm" in sent_lower
                    or "a date you can confirm" in sent_lower
                    or "पुष्टि" in sent_lower
                ):
                    continue
            candidates.append((pos, parsed))

        if not candidates:
            continue

        confirmation_positions = [
            sent_lower.find(phrase)
            for phrase in CONFIRMATION_QUESTION_PHRASES
            if sent_lower.find(phrase) >= 0
        ]
        if confirmation_positions:
            confirmation_position = min(confirmation_positions)
            later_candidates = [
                c for c in candidates
                if c[0] >= confirmation_position
            ]
            if later_candidates:
                return later_candidates[0][1]

        return candidates[-1][1]

    return None


def extract_customer_proposed_date(message: str) -> date | None:
    """Extract a candidate payment date proposed or mentioned by the customer."""
    if not message:
        return None

    raw_candidates = extract_dates_from_text(message)
    if not raw_candidates:
        return None

    candidates: list[tuple[int, date, bool, bool]] = []
    msg_lower = message.lower().translate(DEVANAGARI_DIGITS)

    for pos, parsed, has_year in raw_candidates:
        start_ctx = max(0, pos - 40)
        end_ctx = min(len(msg_lower), pos + 60)
        window = msg_lower[start_ctx:end_ctx]

        is_payment = any(
            kw in window
            for kw in (
                "pay",
                "payment",
                "clear",
                "settle",
                "transfer",
                "confirm",
                "भुगतान",
                "पे",
                "चुका",
                "दूंगा",
                "दूँगा",
                "दूंगी",
                "दूँगी",
                "भर",
                "पुष्टि",
                "वादा",
            )
        )
        is_salary = any(
            kw in window
            for kw in (
                "salary",
                "paycheck",
                "credited",
                "wages",
                "सैलरी",
                "वेतन",
                "तनख्वाह",
            )
        )
        is_pure_salary = is_salary and not is_payment

        candidates.append((pos, parsed, is_payment, is_pure_salary))

    if not candidates:
        return None

    # Prefer dates tied directly to payment
    payment_candidates = [c for c in candidates if c[2]]
    if payment_candidates:
        return payment_candidates[-1][1]

    # Non-pure-salary candidates
    non_salary = [c for c in candidates if not c[3]]
    if non_salary:
        return non_salary[-1][1]

    return None





# ---------------------------------------------------------------------------
# Request and response schemas
# ---------------------------------------------------------------------------

class CustomerMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConversationResponse(BaseModel):
    call_id: int
    customer_id: int
    emi_id: int
    emi_amount: float
    customer_message: str
    agent_reply: str
    extraction: CommitmentExtraction | None = None
    extraction_status: str
    commitment_status: str
    call_status: str
    sentiment: str
    stress_level: str
    sentiment_score: int | None


# ---------------------------------------------------------------------------
def _indicates_no_commitment_or_conversation_end(message: str) -> bool:
    """Check if the customer explicitly declines to commit, indicates inability to provide a date, or requests to end the call."""
    msg = message.lower()
    decline_phrases = (
        "cannot commit",
        "can't commit",
        "cannot provide a date",
        "can't provide a date",
        "don't know when",
        "do not know when",
        "no idea when",
        "cannot say when",
        "can't say when",
        "not sure when",
        "unable to commit",
        "stop calling",
        "don't call",
        "do not call",
        "stop asking",
        "don't ask",
        "do not ask",
        "end the call",
        "hang up",
        "goodbye",
        "bye",
        "that's all",
        "thats all",
        "have no money",
        "don't have money",
        "do not have money",
    )
    return any(p in msg for p in decline_phrases)


def _build_call_summary(
    *,
    extraction: CommitmentExtraction | None,
    call: Call,
    emi_amount: float,
    sentiment: str,
    stress_level: str,
) -> str:
    """Build a concise summary using extracted facts and verified DB state."""
    parts: list[str] = []

    if extraction is not None:
        parts.append(f"Intent: {extraction.intent}")
        if extraction.reason:
            parts.append(f"Reason: {extraction.reason}")
        elif call.reason:
            parts.append(f"Reason: {call.reason}")
    elif call.reason:
        parts.append(f"Reason: {call.reason}")

    parts.append(f"EMI amount: INR {emi_amount:.2f}")
    parts.append(f"Customer sentiment: {sentiment}; stress cue: {stress_level}")

    if call.expected_salary_date is not None:
        parts.append(
            f"Expected salary date: {call.expected_salary_date.isoformat()}"
        )

    if call.promised_payment_date is not None:
        parts.append(
            f"Promised payment date: {call.promised_payment_date.isoformat()}"
        )

    if call.payment_commitment_confirmed:
        parts.append("Payment commitment status: CONFIRMED")
    else:
        parts.append("Payment commitment status: NOT_CONFIRMED")

    if call.pending_payment_date is not None:
        parts.append(
            f"Pending payment date: {call.pending_payment_date.isoformat()}"
        )

    if call.call_status == "COMPLETED":
        parts.append("Call status: COMPLETED")

    return "; ".join(parts)[:10000] or "Conversation processed; no structured details extracted."


def _commitment_response_status(
    *,
    confirmed: bool,
    confirmation_status: ConfirmationStatus,
) -> str:
    if confirmed:
        return "CONFIRMED"
    if confirmation_status == ConfirmationStatus.NOT_CONFIRMED:
        return "DECLINED"
    if confirmation_status == ConfirmationStatus.AMBIGUOUS:
        return "NEEDS_CLARIFICATION"
    return "NOT_CONFIRMED"


# ---------------------------------------------------------------------------
# Conversation endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/{call_id}/message",
    response_model=ConversationResponse,
)
async def send_customer_message(
    call_id: int,
    request: CustomerMessageRequest,
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    if call_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Call ID must be a positive integer",
        )

    customer_message = request.message.strip()
    if not customer_message:
        raise HTTPException(
            status_code=422,
            detail="Message cannot be blank",
        )

    try:
        # Keep all database changes for this request in one transaction.
        # The call row lock prevents concurrent requests from changing the
        # same call's pending/confirmed commitment at the same time.
        result = await db.execute(
            select(Call)
            .where(Call.call_id == call_id)
            .with_for_update()
        )
        call = result.scalar_one_or_none()

        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")

        if call.call_status != "INITIATED":
            raise HTTPException(
                status_code=409,
                detail="This call is not accepting messages",
            )

        customer_result = await db.execute(
            select(Customer).where(Customer.customer_id == call.customer_id)
        )
        customer = customer_result.scalar_one_or_none()

        emi_result = await db.execute(
            select(EMI).where(EMI.emi_id == call.emi_id)
        )
        emi = emi_result.scalar_one_or_none()

        if customer is None or emi is None:
            raise HTTPException(
                status_code=409,
                detail="Call customer or EMI record is unavailable",
            )

        if emi.customer_id != customer.customer_id:
            raise HTTPException(
                status_code=409,
                detail="The EMI does not belong to this customer",
            )

        if emi.status == "PAID":
            raise HTTPException(
                status_code=409,
                detail="This EMI is already marked as paid",
            )

        # The previous agent question must be read before the current
        # customer message is added.
        previous_agent_result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.call_id == call_id,
                ConversationMessage.speaker == "AGENT",
            )
            .order_by(ConversationMessage.message_id.desc())
            .limit(1)
        )
        previous_agent_message = previous_agent_result.scalar_one_or_none()
        previous_agent_text = (
            previous_agent_message.message_text
            if previous_agent_message is not None
            else ""
        )

        confirmation_result = verify_payment_confirmation(
            customer_response=customer_message,
            proposed_payment_date=None,
            agent_last_message=previous_agent_text,
            pending_proposal_date=call.pending_payment_date,
            proposed_payment_amount=emi.emi_amount,
        )

        if confirmation_result.status == ConfirmationStatus.CONFIRMED:
            # Only the confirmation service can authorize this transition.
            call.promised_payment_date = confirmation_result.payment_date
            call.payment_commitment_confirmed = True
            call.pending_payment_date = None
        else:
            # Any prior proposal was declined, ambiguous/tentative, or not pending.
            # Do not retain an obsolete or tentative date as an active confirmation proposal.
            call.pending_payment_date = None

        db.add(
            ConversationMessage(
                call_id=call_id,
                speaker="CUSTOMER",
                message_text=customer_message,
            )
        )
        await db.flush()

        history_result = await db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.call_id == call_id)
            .order_by(ConversationMessage.message_id.asc())
        )
        messages = history_result.scalars().all()
        history = [
            {
                "role": "assistant" if message.speaker == "AGENT" else "user",
                "content": message.message_text,
            }
            for message in messages
            if message.speaker in {"AGENT", "CUSTOMER"}
        ]

        suggested_payment_date_str: str | None = None
        if not call.payment_commitment_confirmed:
            candidate_date = extract_customer_proposed_date(customer_message)
            if candidate_date is not None:
                suggested_payment_date_str = candidate_date.strftime("%d %B %Y").lstrip("0")

        # The AI receives the backend-verified state. It cannot itself
        # authorize or save a payment commitment.
        reply = await generate_agent_reply(
            customer_name=customer.full_name,
            emi_amount=str(emi.emi_amount),
            due_date=emi.due_date.isoformat(),
            conversation_history=history,
            commitment_confirmed=call.payment_commitment_confirmed,
            suggested_payment_date=suggested_payment_date_str,
            preferred_language=customer.preferred_language,
        )

        new_pending_date = extract_pending_payment_date(reply)

        db.add(
            ConversationMessage(
                call_id=call_id,
                speaker="AGENT",
                message_text=reply,
            )
        )

        if call.payment_commitment_confirmed:
            # A verified commitment ends this simulated conversation.
            call.call_status = "COMPLETED"
            call.ended_at = datetime.now(timezone.utc)
            call.pending_payment_date = None
        else:
            # If the agent explicitly asked a date-specific confirmation question
            # in this turn, record that proposed date as awaiting confirmation.
            # Otherwise, ensure pending_payment_date is None.
            call.pending_payment_date = new_pending_date

        await db.flush()

        extraction: CommitmentExtraction | None = None
        extraction_status = "unavailable"

        extraction_history = [
            *history,
            {"role": "assistant", "content": reply},
        ]

        try:
            extraction = await extract_commitment_details(extraction_history)
            extraction_status = "success"
        except (ValueError, RuntimeError):
            logger.warning(
                "Commitment extraction failed for call_id=%s",
                call_id,
            )
        except Exception:
            # Extraction is auxiliary. A failure should not discard a
            # successfully generated reply or a verified commitment.
            logger.exception(
                "Unexpected extraction error for call_id=%s",
                call_id,
            )

        if extraction is not None:
            if extraction.reason is not None:
                call.reason = extraction.reason[:255]

            if extraction.expected_salary_date is not None:
                call.expected_salary_date = extraction.expected_salary_date

            if extraction.intent == "WRONG_NUMBER":
                call.call_status = "COMPLETED"
                call.pending_payment_date = None
                if call.ended_at is None:
                    call.ended_at = datetime.now(timezone.utc)

            elif extraction.intent == "UNAVAILABLE":
                call.call_status = "COMPLETED"
                call.pending_payment_date = None
                if call.ended_at is None:
                    call.ended_at = datetime.now(timezone.utc)
                if not call.reason:
                    call.reason = "Customer unavailable; callback requested"

            elif extraction.intent in {"INSUFFICIENT_FUNDS", "UNABLE_TO_PAY", "REFUSAL"}:
                agent_closed = (
                    "?" not in reply
                    and any(
                        p in reply.lower()
                        for p in (
                            "have a good day",
                            "have a great day",
                            "thank you for your time",
                            "thank you for letting us know",
                            "take care",
                            "goodbye",
                            "have a wonderful day",
                        )
                    )
                )
                customer_declined_or_ended = _indicates_no_commitment_or_conversation_end(customer_message)
                if agent_closed or customer_declined_or_ended:
                    call.call_status = "COMPLETED"
                    call.pending_payment_date = None
                    if call.ended_at is None:
                        call.ended_at = datetime.now(timezone.utc)

        if call.call_status != "COMPLETED":
            # Fallback for UNAVAILABLE if extraction was unavailable
            if any(
                p in customer_message.lower()
                for p in (
                    "busy right now",
                    "cannot talk",
                    "can't talk",
                    "call me later",
                    "call back later",
                    "callback later",
                )
            ):
                call.call_status = "COMPLETED"
                call.pending_payment_date = None
                if call.ended_at is None:
                    call.ended_at = datetime.now(timezone.utc)
                if not call.reason:
                    call.reason = "Customer unavailable; callback requested"

        sentiment_assessment = assess_sentiment(customer_message)
        call.call_summary = _build_call_summary(
            extraction=extraction,
            call=call,
            emi_amount=float(emi.emi_amount),
            sentiment=sentiment_assessment.sentiment,
            stress_level=sentiment_assessment.stress_level,
        )

        # Single commit for the customer message, agent reply, verified
        # commitment, call status, and summary.
        await db.commit()

        commitment_status = _commitment_response_status(
            confirmed=call.payment_commitment_confirmed,
            confirmation_status=confirmation_result.status,
        )

        return ConversationResponse(
            call_id=call_id,
            customer_id=customer.customer_id,
            emi_id=emi.emi_id,
            emi_amount=float(emi.emi_amount),
            customer_message=customer_message,
            agent_reply=reply,
            extraction=extraction,
            extraction_status=extraction_status,
            commitment_status=commitment_status,
            call_status=call.call_status,
            sentiment=sentiment_assessment.sentiment,
            stress_level=sentiment_assessment.stress_level,
            sentiment_score=sentiment_assessment.score,
        )

    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "Database error during conversation for call_id=%s",
            call_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Conversation storage is temporarily unavailable",
        ) from None
    except Exception:
        await db.rollback()
        logger.exception(
            "Conversation processing failed for call_id=%s",
            call_id,
        )
        raise HTTPException(
            status_code=502,
            detail="Conversation processing is temporarily unavailable",
        ) from None


# ---------------------------------------------------------------------------
# Call Summary Endpoint
# ---------------------------------------------------------------------------

class CallSummaryResponse(BaseModel):
    call_id: int
    customer_id: int
    customer_name: str
    emi_id: int
    call_status: str
    payment_commitment_confirmed: bool
    promised_payment_date: date | None
    pending_payment_date: date | None
    reason: str | None
    expected_salary_date: date | None
    call_summary: str
    sentiment: str
    stress_level: str
    sentiment_score: int | None


@router.post(
    "/{call_id}/summary",
    response_model=CallSummaryResponse,
)
async def generate_call_summary(
    call_id: int,
    db: AsyncSession = Depends(get_db),
) -> CallSummaryResponse:
    """
    Build, persist, and return a structured summary of an EMI call idempotently.
    """
    if call_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Call ID must be a positive integer",
        )

    try:
        result = await db.execute(
            select(Call).where(Call.call_id == call_id).with_for_update()
        )
        call = result.scalar_one_or_none()
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")

        customer_result = await db.execute(
            select(Customer).where(Customer.customer_id == call.customer_id)
        )
        customer = customer_result.scalar_one_or_none()

        emi_result = await db.execute(
            select(EMI).where(EMI.emi_id == call.emi_id)
        )
        emi = emi_result.scalar_one_or_none()

        if customer is None or emi is None:
            raise HTTPException(
                status_code=409,
                detail="Call customer or EMI record is unavailable",
            )

        latest_customer_result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.call_id == call_id,
                ConversationMessage.speaker == "CUSTOMER",
            )
            .order_by(ConversationMessage.message_id.desc())
            .limit(1)
        )
        latest_customer_message = latest_customer_result.scalar_one_or_none()
        sentiment = assess_sentiment(
            latest_customer_message.message_text if latest_customer_message else ""
        )

        if not call.call_summary:
            call.call_summary = _build_call_summary(
                extraction=None,
                call=call,
                emi_amount=float(emi.emi_amount),
                sentiment=sentiment.sentiment,
                stress_level=sentiment.stress_level,
            )
            await db.commit()

        return CallSummaryResponse(
            call_id=call.call_id,
            customer_id=customer.customer_id,
            customer_name=customer.full_name,
            emi_id=emi.emi_id,
            call_status=call.call_status,
            payment_commitment_confirmed=call.payment_commitment_confirmed,
            promised_payment_date=call.promised_payment_date,
            pending_payment_date=call.pending_payment_date,
            reason=call.reason,
            expected_salary_date=call.expected_salary_date,
            call_summary=call.call_summary,
            sentiment=sentiment.sentiment,
            stress_level=sentiment.stress_level,
            sentiment_score=sentiment.score,
        )

    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "Database error generating summary for call_id=%s", call_id
        )
        raise HTTPException(
            status_code=503,
            detail="Summary storage is temporarily unavailable",
        ) from None
