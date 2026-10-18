"""Optional OpenAI file-based speech-to-text and text-to-speech adapters."""

import io
import logging
import wave
from functools import lru_cache
import struct

from openai import AsyncOpenAI

from app.config.settings import settings

logger = logging.getLogger(__name__)


def validate_wav(audio: bytes) -> tuple[int, int]:
    """Return (sample_rate, frame_count) for bounded, nonempty PCM WAV audio."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            duration = frames / sample_rate if sample_rate else 0
            if (
                wav_file.getcomptype() != "NONE"
                or frames <= 0
                or sample_rate <= 0
                or channels not in {1, 2}
                or sample_width not in {1, 2, 3, 4}
            ):
                raise ValueError("WAV audio must contain uncompressed PCM samples")
            if duration > settings.local_audio_max_seconds:
                raise ValueError("WAV audio exceeds the configured duration limit")
            pcm = wav_file.readframes(frames)
            if len(pcm) != frames * channels * sample_width:
                raise ValueError("WAV audio data is truncated or malformed")
            return sample_rate, frames
    except (wave.Error, EOFError) as exc:
        raise ValueError("Audio must be a valid PCM WAV file") from exc


@lru_cache(maxsize=1)
def _get_openai_client(api_key: str) -> AsyncOpenAI:
    """Create a lazy client only when an audio feature is actually requested."""
    return AsyncOpenAI(api_key=api_key, timeout=35.0, max_retries=0)


def _client() -> AsyncOpenAI:
    if not settings.has_openai_credentials:
        raise RuntimeError("OpenAI audio is not configured")
    return _get_openai_client(settings.openai_api_key.get_secret_value().strip())


async def transcribe_wav(audio: bytes) -> str:
    """Transcribe one validated WAV file using the configured OpenAI model."""
    validate_wav(audio)
    if len(audio) > settings.local_audio_max_bytes:
        raise ValueError("WAV audio exceeds the configured size limit")
    response = await _client().audio.transcriptions.create(
        file=("customer.wav", audio, "audio/wav"),
        model=settings.openai_stt_model,
        response_format="json",
        timeout=35.0,
    )
    transcript = getattr(response, "text", "")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("No speech was detected in the audio")
    return transcript.strip()


def _decode_mulaw_sample(value: int) -> int:
    value = (~value) & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


def mulaw_to_pcm_wav(audio: bytes, sample_rate: int = 8000) -> bytes:
    """Wrap Twilio μ-law audio as mono PCM16 WAV for file-based Whisper STT."""
    if not audio or len(audio) > settings.local_audio_max_bytes:
        raise ValueError("Telephony audio is empty or exceeds the configured size limit")
    if len(audio) / sample_rate > settings.local_audio_max_seconds:
        raise ValueError("Telephony audio exceeds the configured duration limit")
    pcm = b"".join(struct.pack("<h", _decode_mulaw_sample(sample)) for sample in audio)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


async def transcribe_twilio_mulaw(audio: bytes) -> str:
    return await transcribe_wav(mulaw_to_pcm_wav(audio))


async def synthesize_wav(text: str) -> bytes:
    """Return a validated OpenAI TTS WAV response."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 4000:
        raise ValueError("Text must contain 1 to 4000 characters")
    response = await _client().audio.speech.create(
        model=settings.openai_tts_model,
        voice=settings.openai_tts_voice,
        input=cleaned,
        response_format="wav",
        timeout=35.0,
    )
    audio = response.content
    if not isinstance(audio, bytes) or not audio:
        raise ValueError("OpenAI TTS returned empty audio")
    if len(audio) > settings.local_audio_max_bytes:
        raise ValueError("Generated WAV audio exceeds the configured size limit")
    validate_wav(audio)
    return audio
