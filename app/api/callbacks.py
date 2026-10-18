"""Authenticated scheduling endpoint for customer callbacks."""

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import get_db
from app.models import Call, CallbackSchedule

router = APIRouter(prefix="/calls", tags=["Callbacks"])


class CallbackRequest(BaseModel):
    scheduled_at: datetime = Field(description="Timezone-aware callback date/time")
    reason: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CallbackResponse(BaseModel):
    callback_id: int
    call_id: int
    scheduled_at: datetime
    status: str
    attempt_number: int


@router.post("/{call_id}/callback", response_model=CallbackResponse, status_code=201)
async def schedule_callback(
    call_id: int,
    request: CallbackRequest,
    x_dashboard_key: str | None = Header(default=None, alias="X-Dashboard-Key"),
    db: AsyncSession = Depends(get_db),
) -> CallbackResponse:
    configured_key = (
        settings.dashboard_api_key.get_secret_value()
        if settings.dashboard_api_key
        else ""
    )
    if len(configured_key) < 32:
        raise HTTPException(
            status_code=503,
            detail="Set DASHBOARD_API_KEY to a secret of at least 32 characters before scheduling callbacks",
        )
    if not x_dashboard_key or not hmac.compare_digest(x_dashboard_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid operations key")
    if call_id <= 0:
        raise HTTPException(status_code=422, detail="Call ID must be positive")
    if request.scheduled_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="scheduled_at must include a timezone")

    now = datetime.now(timezone.utc)
    scheduled_at = request.scheduled_at.astimezone(timezone.utc)
    if scheduled_at <= now or scheduled_at > now + timedelta(days=30):
        raise HTTPException(
            status_code=422,
            detail="Callback must be scheduled within the next 30 days",
        )

    result = await db.execute(
        select(Call).where(Call.call_id == call_id).with_for_update()
    )
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.payment_commitment_confirmed:
        raise HTTPException(
            status_code=409,
            detail="A confirmed payment commitment cannot be called back",
        )
    if call.call_status not in {"COMPLETED", "FAILED"}:
        raise HTTPException(
            status_code=409,
            detail="A callback can be scheduled only after the call has ended",
        )
    pending = await db.execute(
        select(CallbackSchedule.callback_id).where(
            CallbackSchedule.call_id == call_id,
            CallbackSchedule.status == "PENDING",
        ).limit(1)
    )
    if pending.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="A callback is already scheduled for this call",
        )

    callback = CallbackSchedule(
        call_id=call_id,
        scheduled_at=scheduled_at.replace(tzinfo=None),
        attempt_number=1,
        reason=request.reason,
        status="PENDING",
    )
    db.add(callback)
    await db.flush()
    response = CallbackResponse(
        callback_id=callback.callback_id,
        call_id=call_id,
        scheduled_at=scheduled_at,
        status=callback.status,
        attempt_number=callback.attempt_number,
    )
    await db.commit()
    return response
