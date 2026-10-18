"""Durable callback/retry dispatcher. Disabled unless explicitly enabled."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.models import Call, CallbackSchedule, ConversationMessage, Customer, EMI
from app.services.voice_service import create_twilio_call

logger = logging.getLogger(__name__)


def _utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def dispatch_due_callbacks_once() -> int:
    """Claim due rows, initiate their outbound calls, and persist outcomes."""
    if not settings.has_twilio_credentials:
        return 0
    claimed: list[tuple[int, int, str, int]] = []
    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                select(CallbackSchedule)
                .where(
                    CallbackSchedule.status == "PENDING",
                    CallbackSchedule.scheduled_at <= _utc_naive(),
                )
                .order_by(CallbackSchedule.scheduled_at, CallbackSchedule.callback_id)
                .with_for_update(skip_locked=True)
                .limit(10)
            )
            schedules = result.scalars().all()
            for schedule in schedules:
                call = await db.get(Call, schedule.call_id)
                if call is None or call.payment_commitment_confirmed:
                    schedule.status = "CANCELLED"
                    continue
                customer = await db.get(Customer, call.customer_id)
                emi = await db.get(EMI, call.emi_id)
                if (
                    customer is None
                    or emi is None
                    or emi.customer_id != call.customer_id
                    or not customer.phone_number
                    or not customer.phone_number.startswith("+")
                ):
                    schedule.status = "FAILED"
                    continue
                schedule.status = "PROCESSING"
                call.call_status = "INITIATED"
                claimed.append(
                    (
                        schedule.callback_id,
                        call.call_id,
                        customer.phone_number,
                        schedule.attempt_number,
                    )
                )

    completed = 0
    for callback_id, call_id, phone_number, attempt_number in claimed:
        async with AsyncSessionLocal() as db:
            try:
                provider_sid = await create_twilio_call(
                    phone_number=phone_number,
                    call_id=call_id,
                    attempt_number=attempt_number,
                )
                schedule = await db.get(CallbackSchedule, callback_id)
                call = await db.get(Call, call_id)
                if schedule:
                    schedule.status = "DISPATCHED"
                    db.add(ConversationMessage(
                        call_id=call_id,
                        speaker="SYSTEM",
                        message_text=f"Callback initiated; provider_call_id={provider_sid}",
                    ))
                if call:
                    call.provider_call_id = provider_sid
                await db.commit()
                completed += 1
            except Exception as exc:
                await db.rollback()
                schedule = await db.get(CallbackSchedule, callback_id)
                call = await db.get(Call, call_id)
                if schedule:
                    schedule.status = "FAILED"
                if call:
                    call.call_status = "FAILED"
                if schedule or call:
                    await db.commit()
                logger.warning("Callback dispatch failed (%s)", type(exc).__name__)
    return completed


async def callback_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await dispatch_due_callbacks_once()
        except Exception as exc:
            # Database URLs and customer data must not appear in scheduler logs.
            logger.warning("Callback worker iteration failed (%s)", type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
