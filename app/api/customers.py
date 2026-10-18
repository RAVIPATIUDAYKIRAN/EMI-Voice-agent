
from datetime import date, datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models import Call, ConversationMessage, Customer, EMI
from app.services.confirmation_service import (
    ConfirmationStatus,
    verify_payment_confirmation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["Customers"])


class EMIResponse(BaseModel):
    emi_id: int
    emi_amount: float
    due_date: str
    status: str


class CustomerEMIResponse(BaseModel):
    customer_id: int
    full_name: str
    preferred_language: str
    emis: list[EMIResponse]

    model_config = ConfigDict(extra="forbid")


@router.get("/{customer_id}/emi", response_model=CustomerEMIResponse)
async def get_customer_emi(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
) -> CustomerEMIResponse:
    if customer_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Customer ID must be a positive integer",
        )

    try:
        customer_result = await db.execute(
            select(Customer).where(
                Customer.customer_id == customer_id
            )
        )
        customer = customer_result.scalar_one_or_none()

        if customer is None:
            raise HTTPException(
                status_code=404,
                detail="Customer not found",
            )

        emi_result = await db.execute(
            select(EMI)
            .where(EMI.customer_id == customer_id)
            .order_by(EMI.due_date.asc())
        )
        emis = emi_result.scalars().all()

        return CustomerEMIResponse(
            customer_id=customer.customer_id,
            full_name=customer.full_name,
            preferred_language=customer.preferred_language,
            emis=[
                EMIResponse(
                    emi_id=emi.emi_id,
                    emi_amount=float(emi.emi_amount),
                    due_date=emi.due_date.isoformat(),
                    status=emi.status,
                )
                for emi in emis
            ],
        )

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Database error while retrieving customer EMI")
        raise HTTPException(
            status_code=503,
            detail="Customer information is temporarily unavailable",
        ) from None


# ---------------------------------------------------------------------------
# Commitment Management Endpoint
# ---------------------------------------------------------------------------

class CustomerCommitmentRequest(BaseModel):
    call_id: int = Field(gt=0, description="Associated call ID")
    promised_payment_date: date | None = Field(
        default=None,
        description="Payment date promised or proposed by the customer",
    )
    reason: str | None = Field(
        default=None,
        max_length=255,
        description="Reason for delay or context",
    )
    expected_salary_date: date | None = Field(
        default=None,
        description="Expected salary date if mentioned",
    )
    is_confirmed: bool = Field(
        default=False,
        description="Whether the customer has explicitly confirmed the commitment",
    )
    confirmation_text: str | None = Field(
        default=None,
        max_length=1000,
        description="Explicit affirmative confirmation statement from the customer",
    )

    model_config = ConfigDict(extra="forbid")


class CustomerCommitmentResponse(BaseModel):
    customer_id: int
    call_id: int
    emi_id: int
    promised_payment_date: date | None
    pending_payment_date: date | None
    payment_commitment_confirmed: bool
    call_status: str
    message: str


@router.post(
    "/{customer_id}/commitment",
    response_model=CustomerCommitmentResponse,
)
async def record_customer_commitment(
    customer_id: int,
    request: CustomerCommitmentRequest,
    db: AsyncSession = Depends(get_db),
) -> CustomerCommitmentResponse:
    """
    Record or finalize a customer payment commitment with strict backend confirmation validation.
    """
    if customer_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Customer ID must be a positive integer",
        )

    try:
        # Validate customer
        customer_result = await db.execute(
            select(Customer).where(Customer.customer_id == customer_id)
        )
        customer = customer_result.scalar_one_or_none()
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Validate call with row lock for transaction safety
        call_result = await db.execute(
            select(Call).where(Call.call_id == request.call_id).with_for_update()
        )
        call = call_result.scalar_one_or_none()
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")

        if call.customer_id != customer_id:
            raise HTTPException(
                status_code=409,
                detail="The call does not belong to this customer",
            )

        # Validate EMI
        emi_result = await db.execute(
            select(EMI).where(EMI.emi_id == call.emi_id)
        )
        emi = emi_result.scalar_one_or_none()
        if emi is None or emi.customer_id != customer_id:
            raise HTTPException(
                status_code=409,
                detail="The EMI does not belong to this customer",
            )

        if emi.status == "PAID":
            raise HTTPException(
                status_code=409,
                detail="This EMI is already marked as paid",
            )

        # Do not allow an unconfirmed request to overwrite an already confirmed commitment
        if call.payment_commitment_confirmed and not request.is_confirmed:
            raise HTTPException(
                status_code=409,
                detail="A payment commitment has already been confirmed for this call",
            )

        # Enforce confirmation safety rules
        if request.is_confirmed:
            if request.promised_payment_date is None:
                raise HTTPException(
                    status_code=422,
                    detail="A promised payment date is required to confirm a commitment",
                )

            if not request.confirmation_text or not request.confirmation_text.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Cannot finalize a commitment without explicit customer confirmation text",
                )

            if call.pending_payment_date is None:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot confirm commitment without a prior pending payment date proposal",
                )

            if request.promised_payment_date != call.pending_payment_date:
                raise HTTPException(
                    status_code=400,
                    detail="The requested payment date does not match the pending proposal date",
                )

            agent_msg_result = await db.execute(
                select(ConversationMessage)
                .where(
                    ConversationMessage.call_id == request.call_id,
                    ConversationMessage.speaker == "AGENT",
                )
                .order_by(ConversationMessage.message_id.desc())
                .limit(1)
            )
            latest_agent_msg = agent_msg_result.scalar_one_or_none()

            if latest_agent_msg is None:
                raise HTTPException(
                    status_code=400,
                    detail="No stored agent confirmation question found for this call",
                )

            verification = verify_payment_confirmation(
                customer_response=request.confirmation_text.strip(),
                proposed_payment_date=request.promised_payment_date,
                agent_last_message=latest_agent_msg.message_text,
                pending_proposal_date=call.pending_payment_date,
            )

            if verification.status != ConfirmationStatus.CONFIRMED:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer statement does not confirm the proposed date: {verification.reason}",
                )

            call.promised_payment_date = verification.payment_date
            call.payment_commitment_confirmed = True
            call.pending_payment_date = None
            if call.call_status == "INITIATED":
                call.call_status = "COMPLETED"
                call.ended_at = datetime.now(timezone.utc)
            message = "Payment commitment explicitly confirmed and recorded."

        else:
            # Unconfirmed request: must not create or replace a pending date proposal
            if request.promised_payment_date is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Payment date proposals cannot be created or replaced via this endpoint. "
                        "Pending payment proposals must be established through the conversation "
                        "workflow where an agent confirmation question is recorded."
                    ),
                )

            message = "Call details recorded without modifying payment date proposals."

        if request.reason is not None:
            call.reason = request.reason[:255]

        if request.expected_salary_date is not None:
            call.expected_salary_date = request.expected_salary_date

        await db.commit()

        return CustomerCommitmentResponse(
            customer_id=customer.customer_id,
            call_id=call.call_id,
            emi_id=call.emi_id,
            promised_payment_date=call.promised_payment_date,
            pending_payment_date=call.pending_payment_date,
            payment_commitment_confirmed=call.payment_commitment_confirmed,
            call_status=call.call_status,
            message=message,
        )

    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception("Database error while recording customer commitment")
        raise HTTPException(
            status_code=503,
            detail="Commitment recording is temporarily unavailable",
        ) from None
