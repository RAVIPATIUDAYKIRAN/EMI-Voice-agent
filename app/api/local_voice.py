"""Optional OpenAI WAV-to-conversation-to-WAV workflow."""

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversations import CustomerMessageRequest, send_customer_message
from app.config.settings import settings
from app.db.database import get_db
from app.services.speech_service import synthesize_wav, transcribe_wav, validate_wav

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calls", tags=["Local voice"])


class LocalVoiceResponse(BaseModel):
    transcript: str
    conversation: dict
    audio_status: str
    audio_wav_base64: str | None = None
    audio_error: str | None = None


async def _read_bounded_audio(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(status_code=415, detail="Content-Type must be audio/wav")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.local_audio_max_bytes:
            raise HTTPException(status_code=413, detail="Audio exceeds the configured size limit")
        chunks.append(chunk)
    if not size:
        raise HTTPException(status_code=422, detail="Audio body is empty")
    return b"".join(chunks)


@router.post(
    "/{call_id}/voice-message",
    response_model=LocalVoiceResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "audio/wav": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def send_local_voice_message(
    call_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LocalVoiceResponse:
    """Transcribe local PCM WAV, run the normal message handler, and synthesize its reply."""
    if call_id <= 0:
        raise HTTPException(status_code=422, detail="Call ID must be a positive integer")
    audio = await _read_bounded_audio(request)
    try:
        transcript = await transcribe_wav(audio)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RuntimeError as exc:
        logger.warning("Local STT unavailable for call_id=%s (%s)", call_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail=(
                "Speech recognition is unavailable. Check OPENAI_API_KEY and "
                "OPENAI_STT_MODEL configuration."
            ),
        ) from None
    except Exception as exc:
        logger.warning("Local STT failed for call_id=%s (%s)", call_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local speech recognition failed") from None

    # Reuse the existing handler so voice turns share its ownership checks,
    # confirmation validation, persistence, and state transitions.
    conversation = await send_customer_message(
        call_id, CustomerMessageRequest(message=transcript), db
    )
    try:
        reply_audio = await synthesize_wav(conversation.agent_reply)
        validate_wav(reply_audio)
    except Exception as exc:
        # The conversation transaction has already committed. Return that
        # truthful result and make the TTS failure explicit so clients can retry
        # synthesis without submitting the customer's audio again.
        logger.warning("Local TTS unavailable after processing call_id=%s (%s)", call_id, type(exc).__name__)
        return LocalVoiceResponse(
            transcript=transcript,
            conversation=conversation.model_dump(mode="json"),
            audio_status="unavailable",
            audio_error=(
                "Conversation was processed, but local TTS did not return valid audio. "
                "Check OPENAI_API_KEY, OPENAI_TTS_MODEL, and OPENAI_TTS_VOICE configuration."
            ),
        )

    return LocalVoiceResponse(
        transcript=transcript,
        conversation=conversation.model_dump(mode="json"),
        audio_status="ready",
        audio_wav_base64=base64.b64encode(reply_audio).decode("ascii"),
    )
