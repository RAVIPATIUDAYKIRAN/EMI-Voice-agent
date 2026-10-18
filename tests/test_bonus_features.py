import base64
import hashlib
import hmac
import json
import io
import wave
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse
from pydantic import SecretStr
from fastapi import HTTPException

from app.api.dashboard import dashboard_data, dashboard_page
from app.api.callbacks import CallbackRequest, schedule_callback
from app.config.settings import settings
from app.services.sentiment_service import assess_sentiment
from app.services.voice_service import (
    create_stream_token,
    create_twilio_call,
    _linear_to_mulaw,
    twiml_for_recording_consent,
    twiml_for_stream,
    start_twilio_recording,
    validate_stream_token,
    validate_twilio_signature,
)
from app.api.telephony import (
    _call_status_from_twilio,
    _decode_twilio_media_event,
    _valid_twilio_start_event,
    _TwilioUtteranceBuffer,
    stream_agent_speech,
    twilio_recording_webhook,
    twilio_status_webhook,
    _call_sid_matches,
)
from app.services.speech_service import mulaw_to_pcm_wav, transcribe_twilio_mulaw
from starlette.requests import Request


def test_bilingual_sentiment_cues_are_bounded_and_non_clinical():
    assert assess_sentiment("I am worried and stressed about money").stress_level == "HIGH"
    hindi = assess_sentiment("मैं बहुत परेशान हूँ, पैसे नहीं हैं")
    assert hindi.sentiment == "DISTRESS_CUE"
    assert hindi.stress_level in {"ELEVATED", "HIGH"}
    assert assess_sentiment("Thanks, I confirm").sentiment == "POSITIVE_CUE"
    unknown = assess_sentiment("Hello")
    assert unknown.sentiment == "UNCERTAIN"
    assert unknown.stress_level == "UNKNOWN"
    assert unknown.score is None


def test_stream_tokens_are_bound_to_call_and_expire(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-secret"))
    token = create_stream_token(25, now=1_000)
    assert validate_stream_token(token, 25, now=1_500)
    assert not validate_stream_token(token, 26, now=1_500)
    assert not validate_stream_token(token, 25, now=1_601)
    assert not validate_stream_token(token + "x", 25, now=1_500)


def test_twilio_signature_verification_uses_constant_time_hmac(monkeypatch):
    token = "local-twilio-token"
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(token))
    url = "https://voice.example.test/telephony/twilio/status/7/1"
    params = {"CallStatus": "completed", "CallSid": "CA123"}
    material = url + "CallSidCA123CallStatuscompleted"
    signature = base64.b64encode(
        hmac.new(token.encode(), material.encode(), hashlib.sha1).digest()
    ).decode()
    assert validate_twilio_signature(url, params, signature)
    assert not validate_twilio_signature(url, params, "invalid")


def test_twilio_status_transition_is_terminal_and_out_of_order_safe():
    assert _call_status_from_twilio("INITIATED", "ringing") == "INITIATED"
    assert _call_status_from_twilio("INITIATED", "completed") == "COMPLETED"
    assert _call_status_from_twilio("COMPLETED", "ringing") == "COMPLETED"
    assert _call_status_from_twilio("FAILED", "completed") == "FAILED"
    assert _call_status_from_twilio("FAILED", "initiated", new_attempt=True) == "INITIATED"
    assert _call_status_from_twilio("FAILED", "completed", new_attempt=True) == "COMPLETED"
    assert _call_status_from_twilio("INITIATED", "unknown") == "INITIATED"


def test_twilio_webhooks_bind_provider_sid_when_call_identity_is_persisted():
    assert _call_sid_matches(SimpleNamespace(provider_call_id=None), "CA-test")
    assert _call_sid_matches(SimpleNamespace(provider_call_id="CA-current"), "CA-current")
    assert not _call_sid_matches(SimpleNamespace(provider_call_id="CA-current"), "CA-other")
    assert not _call_sid_matches(SimpleNamespace(provider_call_id="CA-current"), None)


def test_twilio_stream_start_requires_authenticated_mulaw_8khz_inbound_track(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-secret"))
    token = create_stream_token(27, now=1_000)
    start = {
        "event": "start",
        "streamSid": "MZ-test",
        "start": {
            "customParameters": {"call_id": "27", "token": token},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "tracks": ["inbound"],
        },
    }
    monkeypatch.setattr(
        "app.api.telephony.validate_stream_token",
        lambda value, call_id: validate_stream_token(value, call_id, now=1_100),
    )
    assert _valid_twilio_start_event(start, 27) == (True, "MZ-test")
    wrong_format = {**start, "start": {**start["start"], "mediaFormat": {"encoding": "audio/linear", "sampleRate": 16000, "channels": 1}}}
    assert _valid_twilio_start_event(wrong_format, 27) == (False, None)
    wrong_call = {**start, "start": {**start["start"], "customParameters": {"call_id": "28", "token": token}}}
    assert _valid_twilio_start_event(wrong_call, 27) == (False, None)


def test_twilio_media_decoder_checks_stream_track_encoding_and_size():
    payload = base64.b64encode(bytes(range(160))).decode("ascii")
    event = {
        "event": "media",
        "streamSid": "MZ-test",
        "media": {"track": "inbound", "payload": payload},
    }
    assert _decode_twilio_media_event(event, "MZ-test") == bytes(range(160))
    outbound = {**event, "media": {**event["media"], "track": "outbound"}}
    assert _decode_twilio_media_event(outbound, "MZ-test") is None
    with pytest.raises(ValueError, match="stream identifier"):
        _decode_twilio_media_event({**event, "streamSid": "wrong"}, "MZ-test")
    with pytest.raises(ValueError, match="base64"):
        _decode_twilio_media_event({**event, "media": {"track": "inbound", "payload": "!"}}, "MZ-test")
    with pytest.raises(ValueError, match="oversized"):
        _decode_twilio_media_event({**event, "media": {"track": "inbound", "payload": "A" * 65537}}, "MZ-test")


@pytest.mark.anyio
async def test_twilio_duplicate_status_callback_does_not_duplicate_message(monkeypatch):
    import app.api.telephony as telephony

    monkeypatch.setattr(telephony, "_validate_webhook", lambda request, params: None)
    call = SimpleNamespace(call_id=12, call_status="COMPLETED", payment_commitment_confirmed=False)
    call_result = MagicMock()
    call_result.scalar_one_or_none.return_value = call
    attempt_history = MagicMock()
    attempt_history.scalars.return_value.all.return_value = ["Voice attempt 1: completed"]
    existing_message = MagicMock()
    existing_message.scalar_one_or_none.return_value = 33
    db = AsyncMock()
    db.execute.side_effect = [call_result, attempt_history, existing_message]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telephony, "AsyncSessionLocal", lambda: SessionContext())

    body = b"CallStatus=ringing"
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    request = Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/telephony/twilio/status/12/1",
        "raw_path": b"/telephony/twilio/status/12/1",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
    }, receive)
    response = await twilio_status_webhook(12, 1, request)
    assert response == {"status": "accepted"}
    assert call.call_status == "COMPLETED"
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_twilio_new_attempt_can_advance_after_prior_failed_attempt(monkeypatch):
    import app.api.telephony as telephony

    monkeypatch.setattr(telephony, "_validate_webhook", lambda request, params: None)
    monkeypatch.setattr(settings, "voice_auto_retry_enabled", False)
    call = SimpleNamespace(call_id=13, call_status="FAILED", payment_commitment_confirmed=False)
    call_result = MagicMock()
    call_result.scalar_one_or_none.return_value = call
    attempt_history = MagicMock()
    attempt_history.scalars.return_value.all.return_value = ["Voice attempt 1: no-answer"]
    no_event = MagicMock()
    no_event.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute.side_effect = [call_result, attempt_history, no_event]
    db.add = MagicMock()

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telephony, "AsyncSessionLocal", lambda: SessionContext())
    body = b"CallStatus=completed"
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    request = Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "https",
        "path": "/telephony/twilio/status/13/2", "raw_path": b"/telephony/twilio/status/13/2",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
    }, receive)
    await twilio_status_webhook(13, 2, request)
    assert call.call_status == "COMPLETED"
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_recording_webhook_stores_idempotent_metadata_without_url(monkeypatch):
    import app.api.telephony as telephony

    monkeypatch.setattr(telephony, "_validate_webhook", lambda request, params: None)
    call = SimpleNamespace(call_id=14)
    call_result = MagicMock()
    call_result.scalar_one_or_none.return_value = call
    no_event = MagicMock()
    no_event.scalar_one_or_none.return_value = None
    existing_event = MagicMock()
    existing_event.scalar_one_or_none.return_value = 91
    db = AsyncMock()
    db.execute.side_effect = [call_result, no_event, call_result, existing_event]
    stored = []
    db.add = MagicMock(side_effect=stored.append)

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telephony, "AsyncSessionLocal", lambda: SessionContext())
    request = SimpleNamespace(body=AsyncMock(return_value=(
        b"RecordingStatus=completed&RecordingSid=RE123&RecordingUrl=https%3A%2F%2Frecording.example%2Fprivate"
    )))
    assert await twilio_recording_webhook(14, request) == {"status": "accepted"}
    assert await twilio_recording_webhook(14, request) == {"status": "accepted"}
    assert len(stored) == 1
    assert "Recording completed" in stored[0].message_text
    assert "RE123" in stored[0].message_text
    assert "recording.example" not in stored[0].message_text
    assert db.commit.await_count == 2


def test_twiml_requires_recording_consent_then_uses_signed_bidirectional_stream(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://voice.example.test")
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-secret"))
    consent = ET.fromstring(twiml_for_recording_consent(25, "Hindi"))
    gather = consent.find("Gather")
    consent_say = consent.find("./Gather/Say")
    assert gather is not None and gather.attrib["input"] == "dtmf"
    assert consent_say is not None and "सहमति" in consent_say.text
    assert consent_say.attrib["language"] == "hi-IN"
    root = ET.fromstring(twiml_for_stream(25, "Priya & Co", "Hindi"))
    assert root.find("Say") is not None and "सहमति" in root.find("Say").text
    say = root.find("Say")
    stream = root.find("./Connect/Stream")
    assert say is not None and "Priya & Co" in say.text and "AI" in say.text
    assert stream is not None
    assert stream.attrib["url"] == "wss://voice.example.test/telephony/twilio/stream/25"
    params = {p.attrib["name"]: p.attrib["value"] for p in stream.findall("Parameter")}
    assert params["call_id"] == "25"
    assert validate_stream_token(params["token"], 25)


def test_twilio_utterance_buffer_uses_bounded_silence_terminated_segments():
    collector = _TwilioUtteranceBuffer()
    silence = b"\xff" * 160
    speech = b"\x00" * 160
    for _ in range(10):
        assert collector.push(silence) is None
    assert collector.push(speech) is None
    result = None
    for _ in range(35):
        result = collector.push(silence)
        if result is not None:
            break
    assert result is not None
    assert len(result) == 45 * 160
    assert collector.finish() is None


def test_twilio_segment_size_limit_forces_a_flush(monkeypatch):
    monkeypatch.setattr(settings, "local_audio_max_seconds", 0.03)
    collector = _TwilioUtteranceBuffer()
    assert collector.push(b"\x00" * 160) is None
    segment = collector.push(b"\x00" * 160)
    assert segment is not None
    assert len(segment) == 320


def test_mulaw_audio_is_converted_to_bounded_pcm_wav():
    converted = mulaw_to_pcm_wav(b"\xff" * 160)
    with wave.open(io.BytesIO(converted), "rb") as wav:
        assert wav.getframerate() == 8000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 160
    assert _linear_to_mulaw(0) == 0xFF


@pytest.mark.anyio
async def test_twilio_mulaw_transcription_uses_openai_whisper_file_adapter(monkeypatch):
    import app.services.speech_service as speech

    transcription = AsyncMock(return_value="हाँ, मैं भुगतान करूँगा।")
    monkeypatch.setattr(speech, "transcribe_wav", transcription)
    text = await transcribe_twilio_mulaw(b"\xff" * 160)
    assert text == "हाँ, मैं भुगतान करूँगा।"
    assert transcription.await_args.args[0].startswith(b"RIFF")


@pytest.mark.anyio
async def test_openai_tts_audio_is_resampled_and_chunked_as_twilio_mulaw(monkeypatch):
    import app.services.voice_service as voice

    pcm_wav = io.BytesIO()
    with wave.open(pcm_wav, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x00" * 1050)
    monkeypatch.setattr(voice, "synthesize_wav", AsyncMock(return_value=pcm_wav.getvalue()))
    sent_frames = []
    async def send_audio(payload):
        sent_frames.append(base64.b64decode(payload))

    await stream_agent_speech("नमस्ते", send_audio)
    assert len(sent_frames) == 3
    assert [len(frame) for frame in sent_frames] == [160, 160, 30]
    assert all(frame == b"\xff" * len(frame) for frame in sent_frames)


@pytest.mark.anyio
@pytest.mark.anyio
async def test_twilio_outbound_request_enables_recording_without_network(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "a" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15551234567")
    monkeypatch.setattr(settings, "twilio_allowed_destinations", "+919876543212")
    monkeypatch.setattr(settings, "public_base_url", "https://voice.example.test")

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"sid": "CA" + "b" * 32}
    client = AsyncMock()
    client.post.return_value = response
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    with patch("app.services.voice_service.httpx.AsyncClient", return_value=context):
        sid = await create_twilio_call(phone_number="+919876543212", call_id=6)

    assert sid == "CA" + "b" * 32
    request = client.post.call_args
    assert request.args[0].endswith("/Calls.json")
    assert request.kwargs["data"]["To"] == "+919876543212"
    assert request.kwargs["data"]["Record"] == "false"
    assert "RecordingStatusCallback" not in request.kwargs["data"]


@pytest.mark.anyio
async def test_twilio_outbound_request_rejects_destination_not_in_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "a" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15551234567")
    monkeypatch.setattr(settings, "twilio_allowed_destinations", "+919876543210")
    monkeypatch.setattr(settings, "public_base_url", "https://voice.example.test")
    with pytest.raises(RuntimeError, match="not authorized"):
        await create_twilio_call(phone_number="+919876543212", call_id=6)


@pytest.mark.anyio
async def test_recording_api_targets_the_validated_provider_call(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "a" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("unit-test-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15551234567")
    monkeypatch.setattr(settings, "public_base_url", "https://voice.example.test")
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"sid": "RE" + "c" * 32}
    client = AsyncMock()
    client.post.return_value = response
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    with patch("app.services.voice_service.httpx.AsyncClient", return_value=context):
        sid = await start_twilio_recording(
            provider_call_id="CA" + "b" * 32,
            call_id=6,
        )
    assert sid == "RE" + "c" * 32
    assert client.post.call_args.kwargs["data"]["RecordingStatusCallback"].endswith(
        "/telephony/twilio/recording/6"
    )


@pytest.mark.anyio
async def test_dashboard_requires_key_and_returns_only_linked_call_summary(monkeypatch):
    test_key = "dashboard-test-key-0123456789abcdef"
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr(test_key))
    row = SimpleNamespace(
        call_id=8,
        customer_id=2,
        emi_id=2,
        emi_amount=6200,
        call_status="COMPLETED",
        payment_commitment_confirmed=True,
        promised_payment_date=date(2026, 10, 25),
        pending_payment_date=None,
        reason="Salary delay",
        call_summary="Confirmed for 2026-10-25",
    )
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [row]
    counts = MagicMock()
    counts.one.return_value = (1, 1, 0)
    db.execute.side_effect = [result, counts]

    monkeypatch.setattr(settings, "dashboard_api_key", None)
    with pytest.raises(HTTPException) as missing_configuration:
        await dashboard_data(x_dashboard_key=None, db=db)
    assert missing_configuration.value.status_code == 503
    assert "DASHBOARD_API_KEY" in missing_configuration.value.detail
    db.execute.assert_not_awaited()
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr(test_key))

    with pytest.raises(HTTPException) as missing_key:
        await dashboard_data(x_dashboard_key=None, db=db)
    assert missing_key.value.status_code == 401
    db.execute.assert_not_awaited()

    with pytest.raises(HTTPException) as invalid_key:
        await dashboard_data(x_dashboard_key="wrong-key", db=db)
    assert invalid_key.value.status_code == 401
    db.execute.assert_not_awaited()

    response = await dashboard_data(x_dashboard_key=test_key, db=db)
    assert response["confirmed_commitments"] == 1
    assert response["calls"][0]["customer_id"] == 2
    assert response["calls"][0]["emi_amount"] == 6200.0
    assert response["calls"][0]["promised_payment_date"] == "2026-10-25"
    assert response["calls"][0]["reason"] == "Salary delay"
    assert response["calls"][0]["call_summary"] == "Confirmed for 2026-10-25"
    page = await dashboard_page()
    assert isinstance(page, HTMLResponse)
    assert "X-Dashboard-Key" in page.body.decode()


@pytest.mark.anyio
async def test_callback_scheduling_requires_auth_and_persists_request(monkeypatch):
    test_key = "dashboard-test-key-0123456789abcdef"
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr(test_key))
    call = SimpleNamespace(
        call_id=9,
        payment_commitment_confirmed=False,
        call_status="FAILED",
    )
    call_result = MagicMock()
    call_result.scalar_one_or_none.return_value = call
    no_pending_result = MagicMock()
    no_pending_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute.side_effect = [call_result, no_pending_result]
    db.add = MagicMock(side_effect=lambda row: setattr(row, "callback_id", 19))
    schedule_time = datetime.now(timezone.utc) + timedelta(hours=2)

    monkeypatch.setattr(settings, "dashboard_api_key", None)
    with pytest.raises(HTTPException) as missing_configuration:
        await schedule_callback(
            9,
            CallbackRequest(scheduled_at=schedule_time, reason="Customer requested"),
            x_dashboard_key=None,
            db=db,
        )
    assert missing_configuration.value.status_code == 503
    assert "DASHBOARD_API_KEY" in missing_configuration.value.detail
    db.execute.assert_not_awaited()
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr("too-short"))
    with pytest.raises(HTTPException) as weak_configuration:
        await schedule_callback(
            9,
            CallbackRequest(scheduled_at=schedule_time, reason="Customer requested"),
            x_dashboard_key=None,
            db=db,
        )
    assert weak_configuration.value.status_code == 503
    db.execute.assert_not_awaited()
    monkeypatch.setattr(settings, "dashboard_api_key", SecretStr(test_key))

    with pytest.raises(HTTPException) as unauthorized:
        await schedule_callback(
            9,
            CallbackRequest(scheduled_at=schedule_time, reason="Customer requested"),
            x_dashboard_key=None,
            db=db,
        )
    assert unauthorized.value.status_code == 401
    db.execute.assert_not_awaited()

    with pytest.raises(HTTPException) as invalid_key:
        await schedule_callback(
            9,
            CallbackRequest(scheduled_at=schedule_time, reason="Customer requested"),
            x_dashboard_key="incorrect",
            db=db,
        )
    assert invalid_key.value.status_code == 401
    db.execute.assert_not_awaited()

    response = await schedule_callback(
        9,
        CallbackRequest(scheduled_at=schedule_time, reason="Customer requested"),
        x_dashboard_key=test_key,
        db=db,
    )
    assert response.callback_id == 19
    assert response.call_id == 9
    assert response.status == "PENDING"
    db.commit.assert_awaited_once()
