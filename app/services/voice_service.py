"""Optional Twilio call and media-stream adapters."""

import base64
import asyncio
import hashlib
import hmac
import logging
import re
import struct
import time
import io
import wave

import httpx

from app.config.settings import settings
from app.services.speech_service import synthesize_wav, validate_wav

logger = logging.getLogger(__name__)


def _public_base_url() -> str:
    value = (settings.public_base_url or "").rstrip("/")
    if not value.startswith("https://"):
        raise RuntimeError("PUBLIC_BASE_URL must be a public HTTPS URL")
    return value


def _stream_secret() -> bytes:
    if not settings.twilio_auth_token:
        raise RuntimeError("Twilio credentials are not configured")
    return settings.twilio_auth_token.get_secret_value().encode("utf-8")


def create_stream_token(call_id: int, *, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    payload = f"{call_id}.{issued + 600}".encode("ascii")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_stream_secret(), encoded, hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded.decode('ascii')}.{signed}"


def validate_stream_token(token: str, call_id: int, *, now: int | None = None) -> bool:
    try:
        encoded, signature_text = token.split(".", 1)
        expected = hmac.new(
            _stream_secret(), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        supplied = base64.urlsafe_b64decode(
            signature_text + "=" * (-len(signature_text) % 4)
        )
        if not hmac.compare_digest(expected, supplied):
            return False
        payload = base64.urlsafe_b64decode(
            encoded + "=" * (-len(encoded) % 4)
        )
        payload_call_id, expires_at = payload.decode("ascii").split(".", 1)
        current = int(time.time() if now is None else now)
        return int(payload_call_id) == call_id and current <= int(expires_at)
    except (ValueError, UnicodeError, RuntimeError):
        return False


def validate_twilio_signature(
    url: str, params: dict[str, str], signature: str | None
) -> bool:
    if not signature or not settings.twilio_auth_token:
        return False
    content = url + "".join(key + params[key] for key in sorted(params))
    digest = hmac.new(
        settings.twilio_auth_token.get_secret_value().encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


async def create_twilio_call(
    *, phone_number: str, call_id: int, attempt_number: int = 1
) -> str:
    if not settings.has_twilio_credentials:
        raise RuntimeError("Twilio voice is not configured")
    if phone_number not in settings.authorized_twilio_destinations:
        raise RuntimeError("The outbound destination is not authorized")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone_number):
        raise RuntimeError("The outbound destination is invalid")
    base = _public_base_url()
    account_sid = settings.twilio_account_sid
    auth_token = settings.twilio_auth_token.get_secret_value()
    callback = f"{base}/telephony/twilio/status/{call_id}/{attempt_number}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"
    body = {
        "To": phone_number,
        "From": settings.twilio_from_number,
        "Url": f"{base}/telephony/twilio/voice/{call_id}",
        "Method": "POST",
        "StatusCallback": callback,
        "StatusCallbackMethod": "POST",
        "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        "Record": "false",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            response = await client.post(
                url, data=body, auth=(account_sid, auth_token)
            )
            response.raise_for_status()
            provider_call_id = response.json().get("sid")
            if not isinstance(provider_call_id, str) or not provider_call_id:
                raise RuntimeError("Telephony provider returned no call identifier")
            return provider_call_id
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Twilio call creation failed (%s)", type(exc).__name__)
        raise RuntimeError("Unable to start the voice call") from None


async def start_twilio_recording(*, provider_call_id: str, call_id: int) -> str:
    """Start a recording only after the recipient explicitly consents."""
    if not settings.has_twilio_credentials or not re.fullmatch(
        r"CA[0-9a-fA-F]{32}", provider_call_id
    ):
        raise RuntimeError("Recording is not configured for this call")
    account_sid = settings.twilio_account_sid
    base = _public_base_url()
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/"
        f"{provider_call_id}/Recordings.json"
    )
    body = {
        "RecordingChannels": "dual",
        "RecordingStatusCallback": f"{base}/telephony/twilio/recording/{call_id}",
        "RecordingStatusCallbackMethod": "POST",
        "RecordingStatusCallbackEvent": "completed",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            response = await client.post(
                url,
                data=body,
                auth=(account_sid, settings.twilio_auth_token.get_secret_value()),
            )
            response.raise_for_status()
            recording_sid = response.json().get("sid")
            if not isinstance(recording_sid, str) or not recording_sid:
                raise RuntimeError("Telephony provider returned no recording identifier")
            return recording_sid
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Twilio recording start failed (%s)", type(exc).__name__)
        raise RuntimeError("Unable to start call recording") from None


def twiml_for_recording_consent(call_id: int, language: str) -> str:
    import xml.etree.ElementTree as ET

    base = _public_base_url()
    root = ET.Element("Response")
    gather = ET.SubElement(root, "Gather", {
        "input": "dtmf",
        "numDigits": "1",
        "action": f"{base}/telephony/twilio/consent/{call_id}",
        "method": "POST",
        "timeout": "8",
    })
    is_hindi = language.casefold().startswith("hi")
    say_language = "hi-IN" if is_hindi else "en-IN"
    consent_prompt = (
        "इस कॉल की रिकॉर्डिंग और ट्रांसक्रिप्शन केवल आपकी सहमति के बाद होगी। "
        "सहमति देकर जारी रखने के लिए 1 दबाएँ। अन्यथा कॉल काट दें।"
        if is_hindi
        else "This call will be recorded and transcribed only if you consent. "
        "Press 1 to consent and continue. Otherwise, please hang up."
    )
    ET.SubElement(gather, "Say", {"language": say_language}).text = consent_prompt
    ET.SubElement(root, "Hangup")
    return ET.tostring(root, encoding="unicode")


def twiml_for_stream(call_id: int, customer_name: str, language: str) -> str:
    import xml.etree.ElementTree as ET

    base = _public_base_url()
    stream_url = (
        "wss://" + base.removeprefix("https://")
        + f"/telephony/twilio/stream/{call_id}"
    )
    root = ET.Element("Response")
    is_hindi = language.casefold().startswith("hi")
    say_language = "hi-IN" if is_hindi else "en-IN"
    ET.SubElement(root, "Say", {"language": say_language}).text = (
        f"सहमति देने के लिए धन्यवाद, {customer_name}। मैं एक AI सहायक हूँ। कृपया बताइए, मैं आपकी कैसे सहायता कर सकता हूँ।"
        if is_hindi
        else f"Thank you for your consent, {customer_name}. I am an AI assistant. Please tell me how I can help."
    )
    connect = ET.SubElement(root, "Connect")
    stream = ET.SubElement(connect, "Stream", {"url": stream_url})
    ET.SubElement(
        stream, "Parameter", {"name": "call_id", "value": str(call_id)}
    )
    ET.SubElement(
        stream,
        "Parameter",
        {"name": "token", "value": create_stream_token(call_id)},
    )
    return ET.tostring(root, encoding="unicode")


async def stream_agent_speech(text: str, send_audio) -> None:
    """Generate OpenAI WAV speech and send Twilio-compatible μ-law/8 kHz frames."""
    audio = await synthesize_wav(text[:4000])
    validate_wav(audio)
    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        if wav_file.getcomptype() != "NONE" or wav_file.getsampwidth() != 2:
            raise RuntimeError("Speech audio must be PCM16 WAV")
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        raw = wav_file.readframes(wav_file.getnframes())
    samples = [value[0] for value in struct.iter_unpack("<h", raw)]
    if channels == 2:
        samples = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
    if sample_rate < 8000:
        raise RuntimeError("Speech WAV sample rate cannot be converted to Twilio audio")
    output_samples = _resample_mono(samples, sample_rate, 8000)
    for offset in range(0, len(output_samples), 160):
        frame_samples = output_samples[offset:offset + 160]
        frame = bytes(_linear_to_mulaw(sample) for sample in frame_samples)
        await send_audio(base64.b64encode(frame).decode("ascii"))
        await asyncio.sleep(len(frame_samples) / 8000)


def _resample_mono(samples: list[int], source_rate: int, target_rate: int) -> list[int]:
    """Area-average input PCM into output sample windows (a box low-pass resampler)."""
    output_count = len(samples) * target_rate // source_rate
    output: list[int] = []
    for out_index in range(output_count):
        start = out_index * source_rate / target_rate
        end = (out_index + 1) * source_rate / target_rate
        weighted = 0.0
        cursor = start
        while cursor < end:
            sample_index = int(cursor)
            next_cursor = min(end, sample_index + 1)
            weighted += samples[sample_index] * (next_cursor - cursor)
            cursor = next_cursor
        output.append(round(weighted / (end - start)))
    return output


def _linear_to_mulaw(sample: int) -> int:
    """Encode one PCM16 sample using G.711 μ-law companding."""
    bias = 0x84
    clip = 32635
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), clip) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (magnitude & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF
