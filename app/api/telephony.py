"""Twilio callbacks and bidirectional real-time speech streaming."""

import asyncio
import base64
import json
import logging
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.models import Call, CallbackSchedule, ConversationMessage, Customer, EMI
from app.services.voice_service import (
    stream_agent_speech,
    start_twilio_recording,
    twiml_for_recording_consent,
    twiml_for_stream,
    validate_stream_token,
    validate_twilio_signature,
)
from app.services.speech_service import transcribe_twilio_mulaw

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telephony/twilio", tags=["Telephony"])

_CALL_TERMINAL_STATUSES = {"COMPLETED", "FAILED"}
_SILENCE_THRESHOLD = 500
_SILENCE_END_MS = 700
_PRE_ROLL_FRAMES = 10


def _call_sid_matches(call: Call, provider_call_id: str | None) -> bool:
    """Bind Twilio webhook actions to the provider call stored for this row."""
    stored = getattr(call, "provider_call_id", None)
    return stored is None or (provider_call_id is not None and stored == provider_call_id)


def _call_status_from_twilio(
    current: str, provider_status: str, *, new_attempt: bool = False
) -> str:
    """Apply status monotonicity within an attempt and reset for a newer retry."""
    if current in _CALL_TERMINAL_STATUSES and not new_attempt:
        return current
    if provider_status == "completed":
        return "COMPLETED"
    if provider_status in {"busy", "failed", "no-answer", "canceled"}:
        return "FAILED"
    if provider_status in {"initiated", "queued", "ringing", "answered", "in-progress"}:
        return "INITIATED"
    return current


def _valid_twilio_start_event(event: object, call_id: int) -> tuple[bool, str | None]:
    """Validate Twilio's authenticated stream metadata before forwarding audio."""
    if not isinstance(event, dict):
        return False, None
    start = event.get("start")
    if not isinstance(start, dict):
        return False, None
    custom = start.get("customParameters")
    media_format = start.get("mediaFormat")
    tracks = start.get("tracks")
    if not isinstance(custom, dict) or not isinstance(media_format, dict):
        return False, None
    if str(custom.get("call_id")) != str(call_id):
        return False, None
    if not validate_stream_token(str(custom.get("token", "")), call_id):
        return False, None
    if (
        media_format.get("encoding") != "audio/x-mulaw"
        or media_format.get("sampleRate") != 8000
        or media_format.get("channels") != 1
        or not isinstance(tracks, list)
        or "inbound" not in tracks
    ):
        return False, None
    stream_sid = event.get("streamSid") or start.get("streamSid")
    if not isinstance(stream_sid, str) or not stream_sid:
        return False, None
    return True, stream_sid


def _decode_twilio_media_event(event: object, stream_sid: str) -> bytes | None:
    """Decode one bounded inbound Twilio audio frame; ignore other tracks/events."""
    if not isinstance(event, dict) or event.get("event") != "media":
        return None
    if event.get("streamSid") != stream_sid:
        raise ValueError("Media event stream identifier mismatch")
    media = event.get("media")
    if not isinstance(media, dict):
        raise ValueError("Invalid Twilio media event")
    if media.get("track") != "inbound":
        return None
    payload = media.get("payload")
    if not isinstance(payload, str) or not payload or len(payload) > 65536:
        raise ValueError("Invalid or oversized media payload")
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 media payload") from exc


def _mulaw_rms(audio: bytes) -> float:
    """Estimate inbound μ-law frame energy for silence-based utterance boundaries."""
    if not audio:
        return 0.0
    decoded = []
    for item in audio:
        value = (~item) & 0xFF
        magnitude = (((value & 0x0F) << 3) + 0x84) << ((value >> 4) & 0x07)
        sample = magnitude - 0x84
        decoded.append(-sample if value & 0x80 else sample)
    return (sum(sample * sample for sample in decoded) / len(decoded)) ** 0.5


class _TwilioUtteranceBuffer:
    """Bounded μ-law utterance collector with silence termination and short pre-roll."""

    def __init__(self) -> None:
        self.pre_roll: deque[bytes] = deque(maxlen=_PRE_ROLL_FRAMES)
        self.frames: list[bytes] = []
        self.active = False
        self.silence_ms = 0.0
        self.byte_count = 0

    def push(self, frame: bytes) -> bytes | None:
        if not frame:
            return None
        rms = _mulaw_rms(frame)
        frame_duration_ms = len(frame) * 1000 / 8000
        if not self.active:
            self.pre_roll.append(frame)
            if rms < _SILENCE_THRESHOLD:
                return None
            self.active = True
            self.frames = list(self.pre_roll)
            self.byte_count = sum(map(len, self.frames))
            self.pre_roll.clear()
        else:
            self.frames.append(frame)
            self.byte_count += len(frame)

        self.silence_ms = self.silence_ms + frame_duration_ms if rms < _SILENCE_THRESHOLD else 0.0
        max_bytes = min(settings.local_audio_max_bytes, 25_000_000)
        max_seconds = settings.local_audio_max_seconds
        duration = self.byte_count / 8000
        if self.silence_ms >= _SILENCE_END_MS or self.byte_count >= max_bytes or duration >= max_seconds:
            return self.finish()
        return None

    def finish(self) -> bytes | None:
        result = b"".join(self.frames) if self.active and self.byte_count else None
        self.frames.clear()
        self.active = False
        self.silence_ms = 0.0
        self.byte_count = 0
        self.pre_roll.clear()
        return result


async def _form_values(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    return dict(parse_qsl(raw, keep_blank_values=True))


def _validate_webhook(request: Request, params: dict[str, str]) -> None:
    base = (settings.public_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        raise HTTPException(status_code=503, detail="Voice webhook URL is not configured")
    full_url = base + request.url.path
    if request.url.query:
        full_url += "?" + request.url.query
    if not validate_twilio_signature(
        full_url, params, request.headers.get("X-Twilio-Signature")
    ):
        raise HTTPException(status_code=403, detail="Invalid telephony signature")


@router.post("/voice/{call_id}", response_class=Response, include_in_schema=False)
async def twilio_voice_webhook(call_id: int, request: Request) -> Response:
    params = await _form_values(request)
    _validate_webhook(request, params)
    async with AsyncSessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if not _call_sid_matches(call, params.get("CallSid")):
            raise HTTPException(status_code=403, detail="Invalid telephony call identifier")
        customer = await db.get(Customer, call.customer_id)
        emi = await db.get(EMI, call.emi_id)
        if customer is None or emi is None or emi.customer_id != call.customer_id:
            raise HTTPException(status_code=409, detail="Call customer or EMI is unavailable")
        body = twiml_for_recording_consent(
            call_id, customer.preferred_language or "en"
        )
    return Response(content=body, media_type="application/xml")


@router.post("/consent/{call_id}", response_class=Response, include_in_schema=False)
async def twilio_recording_consent(call_id: int, request: Request) -> Response:
    params = await _form_values(request)
    _validate_webhook(request, params)
    if params.get("Digits") != "1":
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if call is None:
                raise HTTPException(status_code=404, detail="Call not found")
            if not _call_sid_matches(call, params.get("CallSid")):
                raise HTTPException(status_code=403, detail="Invalid telephony call identifier")
            db.add(ConversationMessage(
                call_id=call_id,
                speaker="SYSTEM",
                message_text="Recording consent declined; voice session ended.",
            ))
            call.call_status = "COMPLETED"
            await db.commit()
        return Response(
            content="<Response><Say>Thank you. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    provider_call_id = params.get("CallSid", "")
    try:
        recording_sid = await start_twilio_recording(
            provider_call_id=provider_call_id,
            call_id=call_id,
        )
    except RuntimeError:
        return Response(
            content="<Response><Say>Recording is unavailable, so this call cannot continue. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    async with AsyncSessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if not _call_sid_matches(call, provider_call_id):
            raise HTTPException(status_code=403, detail="Invalid telephony call identifier")
        customer = await db.get(Customer, call.customer_id)
        emi = await db.get(EMI, call.emi_id)
        if customer is None or emi is None or emi.customer_id != call.customer_id:
            raise HTTPException(status_code=409, detail="Call customer or EMI is unavailable")
        db.add(ConversationMessage(
            call_id=call_id,
            speaker="SYSTEM",
            message_text=f"Recording consent accepted; recording_id={recording_sid}",
        ))
        await db.commit()
        body = twiml_for_stream(
            call_id, customer.full_name, customer.preferred_language or "en"
        )
    return Response(content=body, media_type="application/xml")


@router.post("/status/{call_id}/{attempt_number}", include_in_schema=False)
async def twilio_status_webhook(
    call_id: int, attempt_number: int, request: Request
) -> dict[str, str]:
    params = await _form_values(request)
    _validate_webhook(request, params)
    status = params.get("CallStatus", "").casefold()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Call).where(Call.call_id == call_id).with_for_update()
        )
        call = result.scalar_one_or_none()
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if not _call_sid_matches(call, params.get("CallSid")):
            raise HTTPException(status_code=403, detail="Invalid telephony call identifier")
        attempt_history_result = await db.execute(
            select(ConversationMessage.message_text).where(
                ConversationMessage.call_id == call_id,
                ConversationMessage.speaker == "SYSTEM",
                ConversationMessage.message_text.like("Voice attempt %: %"),
            )
        )
        attempt_history = attempt_history_result.scalars().all()
        recorded_attempts = [
            int(match.group(1))
            for text in attempt_history
            if (match := re.match(r"Voice attempt (\d+):", text))
        ]
        latest_attempt = max(recorded_attempts, default=0)
        stale_attempt = attempt_number < latest_attempt
        new_attempt = attempt_number > latest_attempt
        if not stale_attempt:
            call.call_status = _call_status_from_twilio(
                call.call_status, status, new_attempt=new_attempt
            )
        event_text = f"Voice attempt {attempt_number}: {status or 'unknown'}"
        existing_event = await db.execute(
            select(ConversationMessage.message_id)
            .where(
                ConversationMessage.call_id == call_id,
                ConversationMessage.speaker == "SYSTEM",
                ConversationMessage.message_text == event_text,
            )
            .limit(1)
        )
        if existing_event.scalar_one_or_none() is None:
            db.add(ConversationMessage(
                call_id=call_id,
                speaker="SYSTEM",
                message_text=event_text,
            ))
        if (
            status in {"busy", "no-answer", "failed"}
            and not stale_attempt
            and settings.voice_auto_retry_enabled
            and attempt_number < 3
            and not call.payment_commitment_confirmed
        ):
            pending = await db.execute(
                select(CallbackSchedule.callback_id).where(
                    CallbackSchedule.call_id == call_id,
                    CallbackSchedule.status == "PENDING",
                ).limit(1)
            )
            if pending.scalar_one_or_none() is None:
                retry_at = datetime.now(timezone.utc) + timedelta(
                    minutes=5 * attempt_number
                )
                db.add(CallbackSchedule(
                    call_id=call_id,
                    scheduled_at=retry_at.replace(tzinfo=None),
                    attempt_number=attempt_number + 1,
                    reason=f"Automatic retry after {status}",
                    status="PENDING",
                ))
        await db.commit()
    return {"status": "accepted"}


@router.post("/recording/{call_id}", include_in_schema=False)
async def twilio_recording_webhook(call_id: int, request: Request) -> dict[str, str]:
    params = await _form_values(request)
    _validate_webhook(request, params)
    recording_status = params.get("RecordingStatus", "unknown")
    recording_sid = params.get("RecordingSid", "")
    # Keep provider metadata only; do not persist a direct recording URL.
    async with AsyncSessionLocal() as db:
        call_result = await db.execute(
            select(Call).where(Call.call_id == call_id).with_for_update()
        )
        call = call_result.scalar_one_or_none()
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if params.get("CallSid") and not _call_sid_matches(call, params.get("CallSid")):
            raise HTTPException(status_code=403, detail="Invalid telephony call identifier")
        event_text = f"Recording {recording_status}; recording_id={recording_sid}"[:4000]
        existing = await db.execute(
            select(ConversationMessage.message_id).where(
                ConversationMessage.call_id == call_id,
                ConversationMessage.speaker == "SYSTEM",
                ConversationMessage.message_text == event_text,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is None:
            db.add(ConversationMessage(
                call_id=call_id,
                speaker="SYSTEM",
                message_text=event_text,
            ))
        await db.commit()
    return {"status": "accepted"}


@router.websocket("/stream/{call_id}")
async def twilio_media_stream(websocket: WebSocket, call_id: int) -> None:
    if not settings.has_openai_voice_config:
        await websocket.close(code=1013, reason="Streaming speech is not configured")
        return

    await websocket.accept()
    stream_sid: str | None = None
    try:
        # Twilio sends connected then start, which contains authenticated
        # custom parameters from the signed voice webhook response.
        while True:
            event = json.loads(await asyncio.wait_for(websocket.receive_text(), 10))
            if not isinstance(event, dict):
                await websocket.close(code=1008, reason="Invalid stream event")
                return
            if event.get("event") == "start":
                valid_start, stream_sid = _valid_twilio_start_event(event, call_id)
                if not valid_start:
                    await websocket.close(code=1008, reason="Invalid stream token")
                    return
                break
            if event.get("event") == "stop":
                return
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        await websocket.close(code=1008, reason="Invalid stream initialization")
        return

    if not stream_sid:
        await websocket.close(code=1008, reason="Missing stream identifier")
        return

    try:
        from app.api.conversations import CustomerMessageRequest, send_customer_message

        utterances: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=4)
        utterance_buffer = _TwilioUtteranceBuffer()

        async def send_audio(payload: str) -> None:
            await websocket.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": payload},
            })

        async def receive_audio() -> None:
            try:
                while True:
                    event = json.loads(await asyncio.wait_for(websocket.receive_text(), 30))
                    if not isinstance(event, dict):
                        raise ValueError("Invalid Twilio media event")
                    if event.get("event") == "stop":
                        final_segment = utterance_buffer.finish()
                        if final_segment is not None:
                            await utterances.put(final_segment)
                        return
                    audio_frame = _decode_twilio_media_event(event, stream_sid)
                    if audio_frame is not None:
                        segment = utterance_buffer.push(audio_frame)
                        if segment is not None:
                            await utterances.put(segment)
            finally:
                await utterances.put(None)

        async def process_audio() -> None:
            while True:
                segment = await utterances.get()
                if segment is None:
                    return
                transcript = await transcribe_twilio_mulaw(segment)
                if not transcript:
                    continue
                try:
                    async with AsyncSessionLocal() as db:
                        result = await send_customer_message(
                            call_id, CustomerMessageRequest(message=transcript), db
                        )
                    await stream_agent_speech(result.agent_reply, send_audio)
                    if result.call_status == "COMPLETED":
                        await websocket.close(code=1000, reason="Conversation completed")
                        return
                except Exception as exc:
                    logger.warning(
                        "Voice conversation turn failed for call_id=%s (%s)",
                        call_id,
                        type(exc).__name__,
                    )
                    try:
                        await stream_agent_speech(
                            "I am sorry, I could not process that. Please try again later.",
                            send_audio,
                        )
                    except Exception as speech_exc:
                        logger.warning("Voice fallback speech failed (%s)", type(speech_exc).__name__)
                        return

        receive_task = asyncio.create_task(receive_audio())
        process_task = asyncio.create_task(process_audio())
        done, pending = await asyncio.wait(
            {receive_task, process_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if process_task in done:
            receive_task.cancel()
        else:
            # Twilio ended the stream; finish any queued final utterance.
            await process_task
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except (WebSocketDisconnect, asyncio.TimeoutError):
                pass
    except Exception as exc:
        logger.warning("Voice streaming failed (%s)", type(exc).__name__)
        try:
            await websocket.close(code=1011, reason="Voice stream unavailable")
        except RuntimeError:
            pass
