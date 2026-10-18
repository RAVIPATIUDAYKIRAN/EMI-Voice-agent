# Reproducible Demo Scenarios

These examples use the simulated API mode and create new call rows. Run them only against the intended development database after confirming the configured target. They do not delete or modify existing records. The response from call creation supplies the call ID used in every later request.

## Hindi: salary delay and confirmed payment

1. In Swagger, call `GET /customers/2/emi` and verify the database currently returns the intended eligible EMI. The test fixture uses customer 2 / EMI 2 / ₹6,200; live data may differ.
2. Call `POST /customers/2/call`. Keep the returned `call_id`.
3. Send this body to `POST /calls/{call_id}/message`:

```json
{"message":"मेरी सैलरी अभी नहीं आई है। मैं 25 अक्टूबर 2026 को ₹6200 की EMI का भुगतान कर सकता हूँ।"}
```

4. Confirm the response identifies the same customer and EMI, reports the database EMI amount, extracts `2026-10-25` when understood, and asks in Hindi to confirm the exact EMI amount/date. The first turn must remain unconfirmed.
5. Send this body to the same message route:

```json
{"message":"हाँ, मैं 25 अक्टूबर 2026 को ₹6200 की EMI का भुगतान करने की पुष्टि करता हूँ।"}
```

6. A successful turn reports `commitment_status: CONFIRMED` and `call_status: COMPLETED`. Then call `POST /customers/2/commitment` with the same `call_id`, date, `is_confirmed: true`, and the exact confirmation text. The endpoint is idempotent for an already-confirmed matching date.
7. Call `POST /calls/{call_id}/summary`; verify customer/EMI IDs, amount, confirmed date, commitment flag, and summary agree with the persisted call.

Automated coverage: `test_http_english_and_hindi_flow_and_documented_health_routes` with the Hindi case, using an isolated session fake and mocked LLM/extractor. Separately, a live local MySQL/Uvicorn run used fresh call 229 with mocked LLM/extractor and persisted the proposal, confirmation, commitment, and summary; see the verification record below.

## English: salary delay and confirmed payment

Repeat the lookup and create a fresh call. Use:

```json
{"message":"My salary has not arrived yet. I can pay the ₹6,200 EMI on 25 October 2026."}
```

Verify the agent asks to confirm the database-linked amount/date, then send:

```json
{"message":"Yes, I confirm that I will pay the ₹6,200 EMI on 25 October 2026."}
```

Verify `CONFIRMED`, `COMPLETED`, and the final summary, using the same commitment and summary endpoints as in the Hindi scenario. Automated coverage uses the English parametrization of the same isolated API test.

## Refusal, insufficient funds, ambiguity, and conflicting confirmation

Create another fresh simulated call. Send an example such as `I cannot pay right now because I do not have enough funds.` Verify that no payment promise is stored. The precise intent and closure behavior depend on extraction and response context and should be checked in the returned response and summary.

For confirmation safety, create a new proposal and try `शायद`, `नहीं`, a different date, or a different amount instead of the confirmation asked by the agent. Each must remain unconfirmed; the complete-flow tests and confirmation/security unit tests cover these rejection classes. A direct commitment request without a persisted pending proposal is expected to fail.

## Optional OpenAI WAV speech demo

This workflow uses OpenAI's audio APIs and may incur usage charges. It does not place a phone call. Configure these settings in `.env`:

```dotenv
OPENAI_API_KEY=your_openai_api_key
OPENAI_STT_MODEL=whisper-1
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=coral
LOCAL_AUDIO_MAX_BYTES=10000000
LOCAL_AUDIO_MAX_SECONDS=120
```

Create a simulated call, then POST a PCM WAV (maximum defaults: 10 MB and 120 seconds) with `Content-Type: audio/wav` to `/calls/{call_id}/voice-message`. The response includes the recognized transcript, regular conversation-handler response, and OpenAI-generated WAV as base64 JSON. If TTS fails after the conversation handler commits, `audio_status` is `unavailable`; the conversation result remains truthful, and do not resubmit the customer's audio. Invalid WAV returns 422 and oversized input returns 413. OpenAI TTS supports multiple languages, but its voices are optimized for English; Hindi voice quality has not been verified.

Mocked route coverage verifies empty, malformed, unsupported, oversized, over-duration, and truncated audio rejection; empty transcription; STT timeout/provider-error sanitization; Hindi and English transcript delegation to the existing message handler; preservation of conversation-handler failures; WAV response validation; and explicit empty/malformed TTS failure reporting. Tests generate synthetic in-memory PCM WAV bytes and mock the OpenAI SDK. No audio API call was performed during this verification.

The Twilio media bridge validates the signed stream token and inbound μ-law/8 kHz mono format, bounds audio payloads, collects segments using silence boundaries, converts them to PCM WAV for Whisper, forwards transcripts through the existing conversation handler, and converts OpenAI TTS WAV to paced raw μ-law/8 kHz frames for Twilio. Tests mock OpenAI and protocol helpers; they do not verify live provider behavior. Playback `mark` acknowledgements and interruption/barge-in are not implemented. Real silence detection quality and live call resource behavior remain unverified.

## Optional callback and dashboard configuration

Copy `.env.example` to `.env` for a local setup, then set `DASHBOARD_API_KEY` to a generated random secret with at least 32 characters. Keep the real value private. The dashboard-data and callback-scheduling routes return 503 when the setting is absent or too short and 401 when the request header is missing or incorrect. Callback scheduling also requires the unapplied `data/migrations/001_callback_schedules.sql`; review the target database and apply that migration only as an explicitly approved deployment step. A scheduled callback remains `PENDING` until the opt-in worker dispatches it; scheduling alone does not place a call.

## Actual live MySQL smoke verification (2026-10-18)

The configured database endpoint and project-default database were verified as loopback/local before writes. A preflight confirmed customer 2 exists and has eligible EMI 2 for ₹6,200, due `2026-10-20`. The running FastAPI app used the real DB session and routes; only the LLM reply and extraction functions were mocked. Nine new simulated calls (229–237) were appended, with no prior row changed or deleted and no migration applied.

| Call | Scenario | Observed result |
|---|---|---|
| 229 | Hindi salary delay then explicit confirmation | Customer 2 / EMI 2 / ₹6,200; date `2026-10-25`; `CONFIRMED`, `COMPLETED`; commitment and summary persisted. |
| 230 | English salary delay then explicit confirmation | Same linked IDs/amount/date; `CONFIRMED`, `COMPLETED`; commitment and summary persisted. |
| 231 | Hindi rejection | No commitment/date persisted. |
| 232 | Ambiguous response | No commitment/date persisted. |
| 233 | Confirmation of mismatched ₹4,500 | Rejected for confirmation; no commitment/date persisted. |
| 234 | Wrong customer attempts confirmation | HTTP 409; stored call owner remains customer 2; proposal is not committed. |
| 235 | Commitment endpoint without a pending proposal | HTTP 400; no commitment persisted. |
| 236 | Refusal | Call closed; no commitment persisted. |
| 237 | Insufficient funds | Call closed; no commitment persisted. |

The database was queried read-only after stopping the app to verify stored foreign keys, amounts, confirmed flags, dates, call states, and message counts. These results prove sequential persistence against this local development DB with mocked AI; they do not verify live LLM quality, concurrency behavior, provider audio, or deployment databases. These nine simulated test rows remain in the development database intentionally.

## Configured LLM Hindi regression (no MySQL)

The opt-in integration test `tests/integration/test_hindi_configured_llm.py` uses the configured model for real reply generation and extraction, but overrides the DB dependency with an in-memory session. Run it with:

```powershell
$env:RUN_LIVE_LLM_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_hindi_configured_llm.py -q
```

Observed result on 2026-10-18: `1 passed in 14.62s`. The model generated a Hindi date/amount question using “भुगतान कर पाएँगे”; this spelling variation originally prevented saving the pending proposal. The detector now recognizes chandrabindu and anusvara variants. The completed real-LLM run verified the proposal-only first turn, confirmed second turn, commitment route, and summary without connecting to or changing MySQL. Other test suites mock LLM/extraction unless specifically named otherwise.

