
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models import Call, Customer, EMI

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


@router.post(
    "/{customer_id}/call",
    response_model=StartCallResponse,
    status_code=201,
)
async def start_simulated_call(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
) -> StartCallResponse:
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
            .where(
                EMI.customer_id == customer_id,
                EMI.status.in_(["PENDING", "OVERDUE"]),
            )
            .order_by(EMI.due_date.asc())
            .limit(1)
        )
        emi = emi_result.scalar_one_or_none()

        if emi is None:
            raise HTTPException(
                status_code=409,
                detail="No pending or overdue EMI found for this customer",
            )

        call = Call(
            customer_id=customer.customer_id,
            emi_id=emi.emi_id,
            call_status="INITIATED",
            payment_commitment_confirmed=False,
        )

        db.add(call)
        await db.flush()

        response = StartCallResponse(
            call_id=call.call_id,
            customer_id=customer.customer_id,
            customer_name=customer.full_name,
            emi_id=emi.emi_id,
            emi_amount=float(emi.emi_amount),
            due_date=emi.due_date.isoformat(),
            emi_status=emi.status,
            call_status=call.call_status,
            message="Simulated call initialized; no real phone call was placed.",
        )

        await db.commit()
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
