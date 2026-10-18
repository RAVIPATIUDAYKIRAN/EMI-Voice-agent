
import logging
import hmac
import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models import Call, Customer, EMI
from app.config.settings import settings
from app.services.voice_service import create_twilio_call

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["Calls"])


class StartCallResponse(BaseModel):
    call_id: int
    customer_id: int
    customer_name: str
    emi_id: int
    emi_amount: float
    due_date: str
    emi_status: str
    call_status: str
    message: str
    channel: str


@router.post(
    "/{customer_id}/call",
    response_model=StartCallResponse,
    status_code=201,
)
async def start_simulated_call(
    customer_id: int,
    channel: Literal["simulated", "voice"] = Query(default="simulated"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_dashboard_key: str | None = Header(default=None, alias="X-Dashboard-Key"),
    db: AsyncSession = Depends(get_db),
) -> StartCallResponse:
    if customer_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Customer ID must be a positive integer",
        )

    if channel == "voice":
        configured_key = (
            settings.dashboard_api_key.get_secret_value()
            if settings.dashboard_api_key else ""
        )
        if len(configured_key) < 32:
            raise HTTPException(
                status_code=503,
                detail="Set DASHBOARD_API_KEY to a secret of at least 32 characters before placing voice calls",
            )
        if not x_dashboard_key or not hmac.compare_digest(x_dashboard_key, configured_key):
            raise HTTPException(status_code=401, detail="Invalid operations key")

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
            .where(
                EMI.customer_id == customer_id,
                EMI.status.in_(["PENDING", "OVERDUE"]),
            )
            .order_by(EMI.due_date.asc(), EMI.emi_id.asc())
            .limit(1)
        )
        emi = emi_result.scalar_one_or_none()

        if emi is None:
            raise HTTPException(
                status_code=409,
                detail="No pending or overdue EMI found for this customer",
            )

        if emi.customer_id != customer_id:
            raise HTTPException(
                status_code=409,
                detail="The selected EMI does not belong to the requested customer",
            )

        if channel == "voice":
            if not settings.has_twilio_credentials:
                raise HTTPException(
                    status_code=503,
                    detail="Voice calling is not configured",
                )
            if not settings.has_openai_voice_config:
                raise HTTPException(
                    status_code=503,
                    detail="OpenAI speech services are not configured",
                )
            if not customer.phone_number or not re.fullmatch(
                r"\+[1-9]\d{7,14}", customer.phone_number
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Customer phone number must be in E.164 format for voice calls",
                )
            if not settings.authorized_twilio_destinations:
                raise HTTPException(
                    status_code=503,
                    detail="No authorized Twilio destination is configured",
                )
            if customer.phone_number not in settings.authorized_twilio_destinations:
                raise HTTPException(
                    status_code=403,
                    detail="This customer is not an authorized voice-call destination",
                )
            if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", idempotency_key):
                raise HTTPException(
                    status_code=422,
                    detail="Voice calls require a 16-128 character Idempotency-Key",
                )

            prior_result = await db.execute(
                select(Call).where(Call.idempotency_key == idempotency_key)
            )
            prior_call = prior_result.scalar_one_or_none()
            if prior_call is not None:
                if prior_call.customer_id != customer_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key was already used for a different customer",
                    )
                prior_emi_result = await db.execute(
                    select(EMI).where(EMI.emi_id == prior_call.emi_id)
                )
                prior_emi = prior_emi_result.scalar_one_or_none()
                if prior_emi is None or prior_emi.customer_id != customer_id:
                    raise HTTPException(status_code=409, detail="Existing call EMI is unavailable")
                return StartCallResponse(
                    call_id=prior_call.call_id,
                    customer_id=prior_call.customer_id,
                    customer_name=customer.full_name,
                    emi_id=prior_emi.emi_id,
                    emi_amount=float(prior_emi.emi_amount),
                    due_date=prior_emi.due_date.isoformat(),
                    emi_status=prior_emi.status,
                    call_status=prior_call.call_status,
                    message="Existing voice call request returned; no duplicate call was started.",
                    channel="voice",
                )

        call = Call(
            # Persist the requested route identity explicitly. The customer
            # lookup above validates that it exists; it must not be replaced
            # by a default or inferred customer during call creation.
            customer_id=customer_id,
            emi_id=emi.emi_id,
            idempotency_key=idempotency_key if channel == "voice" else None,
            call_status="INITIATED",
            payment_commitment_confirmed=False,
        )

        db.add(call)
        try:
            await db.flush()
        except IntegrityError:
            # A concurrent request may have passed the lookup above. The
            # database's unique key is the final arbiter; recover its winner
            # and never dispatch a second outbound call.
            await db.rollback()
            if channel != "voice" or not idempotency_key:
                raise
            raced_result = await db.execute(
                select(Call).where(Call.idempotency_key == idempotency_key)
            )
            raced_call = raced_result.scalar_one_or_none()
            if raced_call is None:
                raise
            if raced_call.customer_id != customer_id:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different customer",
                )
            raced_emi_result = await db.execute(
                select(EMI).where(EMI.emi_id == raced_call.emi_id)
            )
            raced_emi = raced_emi_result.scalar_one_or_none()
            if raced_emi is None or raced_emi.customer_id != customer_id:
                raise HTTPException(status_code=409, detail="Existing call EMI is unavailable")
            return StartCallResponse(
                call_id=raced_call.call_id,
                customer_id=raced_call.customer_id,
                customer_name=customer.full_name,
                emi_id=raced_emi.emi_id,
                emi_amount=float(raced_emi.emi_amount),
                due_date=raced_emi.due_date.isoformat(),
                emi_status=raced_emi.status,
                call_status=raced_call.call_status,
                message="Existing voice call request returned; no duplicate call was started.",
                channel="voice",
            )

        # Read back the inserted foreign keys before returning the call ID.
        # This makes the response reflect the persisted mapping and fails
        # closed if database-side behavior changed either association.
        await db.refresh(call, attribute_names=["customer_id", "emi_id"])
        if call.customer_id != customer_id or call.emi_id != emi.emi_id:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="The persisted call customer or EMI does not match the request",
            )

        response = StartCallResponse(
            call_id=call.call_id,
            customer_id=call.customer_id,
            customer_name=customer.full_name,
            emi_id=emi.emi_id,
            emi_amount=float(emi.emi_amount),
            due_date=emi.due_date.isoformat(),
            emi_status=emi.status,
            call_status=call.call_status,
            message="Simulated call initialized; no real phone call was placed.",
            channel=channel,
        )

        await db.commit()
        if channel == "voice":
            try:
                provider_call_id = await create_twilio_call(
                    phone_number=customer.phone_number,
                    call_id=call.call_id,
                )
                call.provider_call_id = provider_call_id
                await db.commit()
            except RuntimeError:
                call.call_status = "FAILED"
                await db.commit()
                raise HTTPException(
                    status_code=502,
                    detail="Unable to start the voice call",
                ) from None
            response.message = "Outbound voice call started through the configured provider."
        return response

    except HTTPException:
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception("Database error while starting simulated call")
        raise HTTPException(
            status_code=503,
            detail="Unable to initialize the call",
        ) from None
