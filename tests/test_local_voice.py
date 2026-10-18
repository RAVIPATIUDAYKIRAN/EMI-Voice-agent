import base64
import asyncio
import io
import wave
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.conversations import ConversationResponse
from app.db.database import get_db
from app.main import app
from app.config.settings import settings
from app.services import speech_service
from pydantic import SecretStr
from fastapi import HTTPException


def _wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x01\x00" * 160)
    return output.getvalue()


def _truncated_wav_bytes() -> bytes:
    audio = bytearray(_wav_bytes())
    # Keep the RIFF/data length headers but remove a portion of the declared PCM payload.
    return bytes(audio[:-20])


@pytest.mark.anyio
async def test_local_voice_runs_transcript_through_existing_conversation_and_returns_wav(monkeypatch):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    conversation = ConversationResponse(
        call_id=71,
        customer_id=2,
        emi_id=2,
        emi_amount=6200,
        customer_message="My salary is delayed; I can pay on 25 October.",
        agent_reply="Do you confirm payment on 25 October?",
        extraction=None,
        extraction_status="unavailable",
        commitment_status="NOT_CONFIRMED",
        call_status="INITIATED",
        sentiment="UNCERTAIN",
        stress_level="UNKNOWN",
        sentiment_score=None,
    )
    stt = AsyncMock(return_value=conversation.customer_message)
    orchestrator = AsyncMock(return_value=conversation)
    tts = AsyncMock(return_value=_wav_bytes())
    monkeypatch.setattr(voice_route, "transcribe_wav", stt)
    monkeypatch.setattr(voice_route, "send_customer_message", orchestrator)
    monkeypatch.setattr(voice_route, "synthesize_wav", tts)
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/71/voice-message",
                content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["transcript"] == conversation.customer_message
        assert body["conversation"]["agent_reply"] == conversation.agent_reply
        assert body["audio_status"] == "ready"
        assert base64.b64decode(body["audio_wav_base64"]) == _wav_bytes()
        stt.assert_awaited_once()
        orchestrator.assert_awaited_once()
        assert orchestrator.await_args.args[0] == 71
        assert orchestrator.await_args.args[1].message == conversation.customer_message
        tts.assert_awaited_once_with(conversation.agent_reply)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_local_voice_reports_tts_unavailable_after_truthful_conversation_result(monkeypatch):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    conversation = ConversationResponse(
        call_id=72,
        customer_id=2,
        emi_id=2,
        emi_amount=6200,
        customer_message="Yes.",
        agent_reply="Thank you.",
        extraction=None,
        extraction_status="unavailable",
        commitment_status="NOT_CONFIRMED",
        call_status="INITIATED",
        sentiment="UNCERTAIN",
        stress_level="UNKNOWN",
        sentiment_score=None,
    )
    monkeypatch.setattr(voice_route, "transcribe_wav", AsyncMock(return_value="Yes."))
    monkeypatch.setattr(voice_route, "send_customer_message", AsyncMock(return_value=conversation))
    monkeypatch.setattr(
        voice_route, "synthesize_wav", AsyncMock(side_effect=RuntimeError("not configured"))
    )
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/72/voice-message", content=_wav_bytes(), headers={"content-type": "audio/wav"}
            )
        assert response.status_code == 200
        assert response.json()["conversation"]["call_id"] == 72
        assert response.json()["audio_status"] == "unavailable"
        assert response.json()["audio_wav_base64"] is None
        assert "conversation was processed" in response.json()["audio_error"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_local_voice_does_not_return_empty_tts_audio(monkeypatch):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    conversation = ConversationResponse(
        call_id=76,
        customer_id=2,
        emi_id=2,
        emi_amount=6200,
        customer_message="Hello",
        agent_reply="Please confirm the date.",
        extraction=None,
        extraction_status="unavailable",
        commitment_status="NOT_CONFIRMED",
        call_status="INITIATED",
        sentiment="UNCERTAIN",
        stress_level="UNKNOWN",
        sentiment_score=None,
    )
    monkeypatch.setattr(voice_route, "transcribe_wav", AsyncMock(return_value="Hello"))
    monkeypatch.setattr(voice_route, "send_customer_message", AsyncMock(return_value=conversation))
    monkeypatch.setattr(voice_route, "synthesize_wav", AsyncMock(return_value=b""))
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/76/voice-message", content=_wav_bytes(), headers={"content-type": "audio/wav"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["audio_status"] == "unavailable"
        assert body["audio_wav_base64"] is None
        assert "valid audio" in body["audio_error"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
@pytest.mark.parametrize("transcript", [
    "मेरी सैलरी अभी नहीं आई है। मैं 25 अक्टूबर को भुगतान कर सकता हूँ।",
    "My salary is delayed. I can pay on 25 October.",
])
async def test_local_voice_passes_hindi_and_english_transcripts_to_shared_handler(monkeypatch, transcript):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    conversation = ConversationResponse(
        call_id=78, customer_id=2, emi_id=2, emi_amount=6200,
        customer_message=transcript, agent_reply="Please confirm the payment date.",
        extraction=None, extraction_status="unavailable",
        commitment_status="NOT_CONFIRMED", call_status="INITIATED",
        sentiment="UNCERTAIN", stress_level="UNKNOWN", sentiment_score=None,
    )
    handler = AsyncMock(return_value=conversation)
    monkeypatch.setattr(voice_route, "transcribe_wav", AsyncMock(return_value=transcript))
    monkeypatch.setattr(voice_route, "send_customer_message", handler)
    monkeypatch.setattr(voice_route, "synthesize_wav", AsyncMock(return_value=_wav_bytes()))
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/78/voice-message", content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert base64.b64decode(response.json()["audio_wav_base64"]) == _wav_bytes()
        assert response.json()["transcript"] == transcript
        assert handler.await_args.args[1].message == transcript
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [(asyncio.TimeoutError(), 503), (RuntimeError("private backend detail"), 503)],
)
async def test_local_voice_sanitizes_stt_timeout_and_provider_failures(
    monkeypatch, failure, expected_status
):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    stt = AsyncMock(side_effect=failure)
    monkeypatch.setattr(voice_route, "transcribe_wav", stt)
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/79/voice-message", content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == expected_status
        assert "private backend detail" not in response.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_local_voice_empty_transcript_is_rejected_without_conversation(monkeypatch):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    handler = AsyncMock()
    monkeypatch.setattr(
        voice_route, "transcribe_wav", AsyncMock(side_effect=ValueError("No speech was detected in the audio"))
    )
    monkeypatch.setattr(voice_route, "send_customer_message", handler)
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/80/voice-message", content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 422
        handler.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_local_voice_preserves_conversation_handler_failure_and_skips_tts(monkeypatch):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    tts = AsyncMock()
    monkeypatch.setattr(voice_route, "transcribe_wav", AsyncMock(return_value="Yes, I confirm."))
    monkeypatch.setattr(
        voice_route, "send_customer_message",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="Conversation processing is temporarily unavailable")),
    )
    monkeypatch.setattr(voice_route, "synthesize_wav", tts)
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/81/voice-message", content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 502
        assert response.json()["detail"] == "Conversation processing is temporarily unavailable"
        tts.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_audio", [b"not a wave", b""])
async def test_local_voice_marks_malformed_or_empty_tts_output_unavailable(monkeypatch, invalid_audio):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    conversation = ConversationResponse(
        call_id=82, customer_id=2, emi_id=2, emi_amount=6200,
        customer_message="Hello", agent_reply="Please confirm.", extraction=None,
        extraction_status="unavailable", commitment_status="NOT_CONFIRMED",
        call_status="INITIATED", sentiment="UNCERTAIN", stress_level="UNKNOWN",
        sentiment_score=None,
    )
    monkeypatch.setattr(voice_route, "transcribe_wav", AsyncMock(return_value="Hello"))
    monkeypatch.setattr(voice_route, "send_customer_message", AsyncMock(return_value=conversation))
    monkeypatch.setattr(voice_route, "synthesize_wav", AsyncMock(return_value=invalid_audio))
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/82/voice-message", content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 200
        assert response.json()["audio_status"] == "unavailable"
        assert response.json()["audio_wav_base64"] is None
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_local_voice_rejects_non_wav_content_before_transcription(monkeypatch):
    import app.api.local_voice as voice_route

    stt = AsyncMock()
    monkeypatch.setattr(voice_route, "transcribe_wav", stt)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/calls/71/voice-message", content=b"not audio", headers={"content-type": "text/plain"}
        )
    assert response.status_code == 415
    stt.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "content_type", "limit", "expected_status"),
    [
        (b"", "audio/wav", None, 422),
        (_wav_bytes(), "audio/wav", 32, 413),
        (_wav_bytes(), "text/plain", None, 415),
    ],
)
async def test_local_voice_rejects_empty_oversized_or_unsupported_audio(
    monkeypatch, body, content_type, limit, expected_status
):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    stt = AsyncMock()
    monkeypatch.setattr(voice_route, "transcribe_wav", stt)
    if limit is not None:
        monkeypatch.setattr(settings, "local_audio_max_bytes", limit)
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/73/voice-message",
                content=body,
                headers={"content-type": content_type},
            )
        assert response.status_code == expected_status
        stt.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
@pytest.mark.parametrize("too_long", [False, True])
async def test_local_voice_rejects_malformed_or_excessive_duration_wav(monkeypatch, too_long):
    async def isolated_db():
        yield object()

    if too_long:
        monkeypatch.setattr(settings, "local_audio_max_seconds", 0)
        body = _wav_bytes()
    else:
        body = b"not a WAV"
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/75/voice-message",
                content=body,
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_local_voice_does_not_expose_stt_runtime_error_details(monkeypatch):
    import app.api.local_voice as voice_route

    async def isolated_db():
        yield object()

    async def failed_stt(_audio):
        raise RuntimeError("internal model path and provider token must stay private")

    monkeypatch.setattr(voice_route, "transcribe_wav", failed_stt)
    app.dependency_overrides[get_db] = isolated_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/calls/74/voice-message",
                content=_wav_bytes(),
                headers={"content-type": "audio/wav"},
            )
        assert response.status_code == 503
        assert "OPENAI_STT_MODEL" in response.json()["detail"]
        assert "provider token" not in response.text
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_local_voice_rejects_truncated_pcm_frames():
    with pytest.raises(ValueError, match="truncated or malformed"):
        speech_service.validate_wav(_truncated_wav_bytes())


def test_local_voice_enforces_configured_audio_duration(monkeypatch):
    monkeypatch.setattr(settings, "local_audio_max_seconds", 0)
    with pytest.raises(ValueError, match="duration limit"):
        speech_service.validate_wav(_wav_bytes())


@pytest.mark.anyio
async def test_openai_whisper_adapter_uses_configured_model_and_returns_transcript(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("local-test-key"))
    transcription = AsyncMock(return_value=type("Result", (), {"text": " नमस्ते "})())
    fake_client = type("Client", (), {
        "audio": type("Audio", (), {
            "transcriptions": type("Transcriptions", (), {"create": transcription})()
        })()
    })()
    monkeypatch.setattr(speech_service, "_client", lambda: fake_client)
    assert await speech_service.transcribe_wav(_wav_bytes()) == "नमस्ते"
    args = transcription.call_args.kwargs
    assert args["model"] == settings.openai_stt_model
    assert args["response_format"] == "json"
    assert args["file"][0] == "customer.wav"


@pytest.mark.anyio
async def test_openai_tts_adapter_uses_configured_model_voice_and_validates_wav(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("local-test-key"))
    speech = AsyncMock(return_value=type("Result", (), {"content": _wav_bytes()})())
    fake_client = type("Client", (), {
        "audio": type("Audio", (), {
            "speech": type("Speech", (), {"create": speech})()
        })()
    })()
    monkeypatch.setattr(speech_service, "_client", lambda: fake_client)
    result = await speech_service.synthesize_wav("नमस्ते, भुगतान तिथि की पुष्टि करें।")
    assert speech_service.validate_wav(result) == (16_000, 160)
    assert speech.call_args.kwargs["model"] == settings.openai_tts_model
    assert speech.call_args.kwargs["voice"] == settings.openai_tts_voice
    assert speech.call_args.kwargs["response_format"] == "wav"


@pytest.mark.anyio
async def test_speech_service_requires_openai_credentials_only_when_invoked(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    with pytest.raises(RuntimeError, match="OpenAI audio is not configured"):
        await speech_service.transcribe_wav(_wav_bytes())


@pytest.mark.anyio
async def test_openai_transcription_rejects_empty_transcript(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("local-test-key"))
    transcription = AsyncMock(return_value=type("Result", (), {"text": "  "})())
    fake_client = type("Client", (), {
        "audio": type("Audio", (), {
            "transcriptions": type("Transcriptions", (), {"create": transcription})()
        })()
    })()
    monkeypatch.setattr(speech_service, "_client", lambda: fake_client)
    with pytest.raises(ValueError, match="No speech was detected"):
        await speech_service.transcribe_wav(_wav_bytes())
