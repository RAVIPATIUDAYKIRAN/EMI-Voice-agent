API Documentation — AI EMI Collection Voice Agent
Base URL: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
Scope: Simulated text calls remain the default. Optional voice calls use Twilio outbound calling and bidirectional Media Streams with bounded, file-based OpenAI Whisper transcription and OpenAI TTS. They require provider credentials, an allowlisted destination, an `Idempotency-Key`, a public HTTPS/WSS base URL, and explicit recording consent.

Complete API Endpoint Inventory
Method	Endpoint	Description
GET	/health	Check application service availability
GET	/health/database	Check MySQL connectivity
GET	/customers/{customer_id}/emi	Retrieve customer and associated EMI details
POST	/customers/{customer_id}/call	Start a simulated call session
POST	/calls/{call_id}/message	Process a customer text message in a conversation
POST	/calls/{call_id}/voice-message	Transcribe PCM WAV with OpenAI Whisper, process through the conversation handler, and return OpenAI TTS WAV
POST	/customers/{customer_id}/commitment	Record non-date details or finalize an existing pending payment commitment
POST	/calls/{call_id}/summary	Generate or retrieve a call summary
POST	/calls/{call_id}/callback	Schedule an authenticated callback
GET	/dashboard	Operations dashboard page
GET	/dashboard/data	Authenticated call and commitment overview


Request and response schemas shown below are illustrative. Confirm the current schemas and status codes in the running Swagger UI (/docs) before relying on them as an exact contract.
1. Application Health Check
GET /health
Checks whether the FastAPI application is responding.
Example response (200 OK):
{
  "status": "ok"
}
2. Database Health Check
GET /health/database
Checks database connectivity. The response should not include credentials.
Example successful response (200 OK):
{
  "status": "ok",
  "database": "emi_voice_agent"
}
Example unavailable response (503 Service Unavailable):
{
  "detail": "Database is unavailable"
}
The database name above is an example and depends on the local MYSQL_DATABASE configuration.
3. Retrieve Customer and EMI Details
GET /customers/{customer_id}/emi
Retrieves a customer record and its associated EMI records from MySQL.
Path parameter
- customer_id — integer customer identifier. The API is intended to accept positive IDs.
Example response (200 OK):
{
  "customer_id": 1,
  "full_name": "Rahul Sharma",
  "preferred_language": "English",
  "emis": [
    {
      "emi_id": 1,
      "emi_amount": 8500.0,
      "due_date": "2026-10-10",
      "status": "OVERDUE"
    }
  ]
}
The example values are fictional sample data. A successful lookup confirms that a matching database record exists; it is not identity verification.
Possible error responses (confirm exact status codes and messages in /docs):
- 404 Not Found — customer does not exist.
- 422 Unprocessable Entity — invalid customer ID.
- 503 Service Unavailable — database/query failure, if handled by the implementation.
4. Initiate a Simulated Outbound Call
POST /customers/{customer_id}/call
Starts a call session for a customer with an eligible pending or overdue EMI, selected from that customer's EMI records by earliest due date and then EMI ID. The optional query parameter `channel` defaults to `simulated`; `channel=voice` requests an outbound Twilio call and requires configured voice providers and a public base URL. The response reports the stored customer and EMI foreign keys and the linked amount.
Path parameter
- customer_id — integer customer identifier.
Query parameter
- channel — `simulated` (default) or `voice`.
Voice-only headers — `X-Dashboard-Key` must match a configured 32+ character operations key; `Idempotency-Key` must be 16–128 characters using letters, digits, `.`, `_`, `:`, or `-`. Voice is rejected unless the customer phone is in `TWILIO_ALLOWED_DESTINATIONS`. Existing databases must first receive migration 002. Repeating the same idempotency key for the same customer returns the original call without another Twilio create request.
Example response (201 Created):
{
  "call_id": 25,
  "customer_id": 2,
  "customer_name": "Priya Reddy",
  "emi_id": 2,
  "emi_amount": 6200.0,
  "due_date": "2026-10-20",
  "emi_status": "PENDING",
  "call_status": "INITIATED",
  "message": "Simulated call initialized; no real phone call was placed.",
  "channel": "simulated"
}
This is an illustrative response; IDs and values depend on the database.
Possible error responses (confirm exact status codes in /docs):
- 404 Not Found — customer does not exist.
- 409 Conflict — no eligible EMI is available for the call.
- 409 Conflict — invalid phone number for voice mode or persisted association mismatch.
- 422 Unprocessable Entity — invalid customer ID.
- 502 Bad Gateway — configured telephony provider did not start the call.
- 503 Service Unavailable — database failure, if handled by the implementation.
5. Send a Customer Message
POST /calls/{call_id}/message
Processes a customer text message as a conversational turn. The application may extract intent, reason, and date information and apply backend confirmation rules. This endpoint does not accept live audio in the current MVP.
Path parameter
- call_id — call session identifier.
Example request body
{
  "message": "My salary is delayed. I will try to pay on 28 October 2026, but I cannot guarantee it."
}
The exact request constraints are defined by the request schema in the running API documentation. For real voice, use POST /customers/{customer_id}/call?channel=voice. Twilio must be able to reach PUBLIC_BASE_URL. Recording and streaming begin only after the recipient presses 1 to consent.
Illustrative response: tentative date
{
  "call_id": 25,
  "customer_id": 2,
  "emi_id": 2,
  "emi_amount": 6200.0,
  "customer_message": "My salary is delayed. I will try to pay on 28 October 2026, but I cannot guarantee it.",
  "agent_reply": "I understand that the date is tentative. Please let us know when you have a clearer idea of the payment timing.",
  "extraction": {
    "reason": "Salary is delayed.",
    "expected_salary_date": null,
    "proposed_payment_date": "2026-10-28",
    "intent": "SALARY_DELAY",
    "needs_confirmation": true
  },
  "extraction_status": "success",
  "commitment_status": "NOT_CONFIRMED",
  "call_status": "INITIATED",
  "sentiment": "UNCERTAIN",
  "stress_level": "UNKNOWN",
  "sentiment_score": null
}
This example illustrates an unconfirmed proposal only. A tentative statement such as “I will try” or “I cannot guarantee it” must not be described as a confirmed commitment.
Illustrative response: explicit confirmation
{
  "call_id": 25,
  "customer_id": 2,
  "emi_id": 2,
  "emi_amount": 6200.0,
  "customer_message": "Yes, I will pay on 28 October 2026.",
  "agent_reply": "Thank you. Your payment commitment for 28 October 2026 has been confirmed.",
  "extraction": {
    "reason": null,
    "expected_salary_date": null,
    "proposed_payment_date": "2026-10-28",
    "intent": "PROMISE_TO_PAY",
    "needs_confirmation": false
  },
  "extraction_status": "success",
  "commitment_status": "CONFIRMED",
  "call_status": "COMPLETED",
  "sentiment": "POSITIVE_CUE",
  "stress_level": "UNKNOWN",
  "sentiment_score": 1
}
The actual response text and fields may vary with the implementation and configured LLM.
Possible error responses (confirm exact status codes and messages in /docs):
- 404 Not Found — call does not exist.
- 409 Conflict — call is no longer eligible for additional messages, or the EMI is not eligible.
- 422 Unprocessable Entity — invalid call ID or request body.
- 502 Bad Gateway — upstream AI service failure, if mapped by the implementation.
- 503 Service Unavailable — database/persistence failure, if mapped by the implementation.
5a. Process a Local Voice Turn
POST /calls/{call_id}/voice-message
Accepts a raw PCM WAV request body with `Content-Type: audio/wav`, validates the WAV header and PCM payload, channels, sample width, duration and configured byte limit, transcribes it with the configured OpenAI Whisper model, delegates the transcript to the existing conversation handler, and synthesizes/validates the agent reply as WAV using the configured OpenAI TTS model and voice. It returns JSON containing the transcript, the normal conversation response, and base64-encoded WAV audio. This route never places a phone call. Internal provider exception details are not returned to callers.

Configure `OPENAI_API_KEY`, `OPENAI_STT_MODEL` (default `whisper-1`), `OPENAI_TTS_MODEL` (default `gpt-4o-mini-tts`), and `OPENAI_TTS_VOICE` (default `coral`). Text simulation does not require speech credentials. `LOCAL_AUDIO_MAX_BYTES` and `LOCAL_AUDIO_MAX_SECONDS` bound audio input and output. Missing STT setup returns 503. If TTS fails after the message transaction is committed, the response reports `audio_status: unavailable` while preserving the truthful conversation result; do not resubmit that customer audio. The TTS voices are optimized for English; Hindi audio quality has not been verified.

Possible errors: 415 for a non-WAV content type, 413 for excessive size, 422 for invalid/empty/too-long WAV or no detected speech, and 503 when OpenAI speech configuration or service is unavailable.

6. Record or Finalize a Customer Commitment
POST /customers/{customer_id}/commitment
Finalizes an existing pending payment commitment or records non-date details (e.g., reason or expected salary date).

Important Lifecycle and Security Rules:
- This endpoint cannot create or replace a pending payment date. Pending payment proposals must be established through the conversation workflow (`POST /calls/{call_id}/message`), where the date-specific agent question and pending date are committed in the same database transaction.
- When `is_confirmed` is `false`, any request that supplies a payment date (`promised_payment_date`) is rejected with `409 Conflict`.
- When `is_confirmed` is `false` and `promised_payment_date` is `null`, permitted non-date details (`reason`, `expected_salary_date`) are saved without altering or clearing any existing pending proposal date.
- When `is_confirmed` is `true`, confirmation requires:
  1. An existing pending proposal date (`call.pending_payment_date`) on the call.
  2. The requested `promised_payment_date` must exactly match the pending proposal date.
  3. The latest stored agent message for the call must be retrieved from `conversation_messages` (`speaker == "AGENT"`).
  4. Non-empty confirmation text must be provided.
  5. The confirmation service (`verify_payment_confirmation`) must validate that the agent message asked for confirmation of that pending date and the customer response affirmatively confirms it.
  6. Ambiguous, tentative, or conflicting date statements are rejected with `400 Bad Request`.
  7. On valid confirmation, `promised_payment_date` is saved, `payment_commitment_confirmed` is set to `true`, `pending_payment_date` is cleared (`null`), and the call is marked `COMPLETED` (if currently `INITIATED`).
- An unconfirmed request cannot reset or overwrite an already confirmed commitment (`409 Conflict`).

Path parameter
- customer_id — customer identifier.
Illustrative request body
{
  "call_id": 25,
  "promised_payment_date": "2026-10-28",
  "reason": "Salary received on 27 October",
  "expected_salary_date": "2026-10-27",
  "is_confirmed": true,
  "confirmation_text": "Yes, I confirm that I will pay on 28 October 2026."
}
Illustrative successful response
{
  "customer_id": 2,
  "call_id": 25,
  "emi_id": 2,
  "promised_payment_date": "2026-10-28",
  "pending_payment_date": null,
  "payment_commitment_confirmed": true,
  "call_status": "COMPLETED",
  "message": "Payment commitment explicitly confirmed and recorded."
}
Possible error responses (confirm exact status codes in /docs):
- 400 Bad Request — confirmation text is missing, call has no pending proposal date, requested date does not match pending date, no stored agent confirmation question found, or customer statement does not confirm the proposed date.
- 404 Not Found — customer or call does not exist.
- 409 Conflict — attempt to create or replace a pending date proposal via this endpoint (`is_confirmed=false` with a date), attempt to overwrite an already confirmed commitment, call does not belong to customer, EMI does not belong to customer, or EMI is already paid.
- 422 Unprocessable Entity — invalid ID or missing required promised payment date when `is_confirmed=true`.
- 503 Service Unavailable — database error during commitment recording.
A confirmed commitment records the customer's stated intention. It does not prove that payment was received and does not authorize a bank debit.

## Optional operations features

### Schedule a callback

`POST /calls/{call_id}/callback` requires the `X-Dashboard-Key` header. Configure `DASHBOARD_API_KEY` with a randomly generated secret of at least 32 characters. If it is missing or too short, the route fails closed with HTTP 503 and an actionable configuration message; a missing or incorrect request header returns HTTP 401. The request must contain a timezone-aware future `scheduled_at` (within 30 days) and a short `reason`. Scheduling is allowed only for an ended call without a confirmed payment commitment, and a call may have only one pending callback. Scheduling creates a pending callback record; it does not itself place another call. Dispatch is performed by the worker only when `CALLBACK_WORKER_ENABLED=true` and Twilio is configured.

```json
{
  "scheduled_at": "2026-10-25T10:00:00+05:30",
  "reason": "Customer requested a callback"
}
```

The callback table migration is `data/migrations/001_callback_schedules.sql`.

### Operations dashboard

`GET /dashboard` serves the dashboard page. `GET /dashboard/data` requires `X-Dashboard-Key` and returns recent call/commitment records, pending and promised dates, reason, persisted summary, and aggregate counts. The dashboard data endpoint is read-only. Configure the same 32+ character `DASHBOARD_API_KEY` before using either callback scheduling or dashboard data. See `.env.example` for placeholder variable names; never commit a real `.env` file.

### Twilio voice webhooks

Voice webhook URLs are generated for configured Twilio calls and are not intended for manual invocation. Twilio webhook signatures are checked. The recipient hears a language-specific recording disclosure and must press 1 before recording or streaming starts. Transcribed turns are processed through the same `/calls/{call_id}/message` handler used by text conversations. Real-provider behavior has not been validated by the isolated test suite.

Voice call creation also requires an `Idempotency-Key` header (16–128 alphanumeric or `._:-` characters) and a customer phone number present in `TWILIO_ALLOWED_DESTINATIONS`. Repeating the same key for the same customer returns the existing call without issuing another provider request. Apply `data/migrations/002_call_voice_idempotency.sql` before enabling this feature on an existing MySQL database; migrations are not run automatically.

Twilio inbound μ-law/8 kHz frames are buffered into bounded speech segments using a short silence boundary. Each completed segment is decoded to PCM WAV and submitted to the configured file transcription model. Agent WAV output is converted to raw μ-law/8 kHz media frames. Whisper transcription is turn-based; `whisper-1` does not provide a live transcription WebSocket. Provider delivery, silence thresholds, playback marks, and interruption handling remain unverified or unsupported.
7. Generate or Retrieve a Call Summary
POST /calls/{call_id}/summary
Requests a call summary for the specified call. The summary should reflect persisted call state and must not upgrade an unconfirmed proposal to a confirmed commitment.
Path parameter
- call_id — call session identifier.
Illustrative response
{
  "call_id": 25,
  "customer_id": 2,
  "customer_name": "Priya Reddy",
  "emi_id": 2,
  "call_status": "COMPLETED",
  "payment_commitment_confirmed": true,
  "promised_payment_date": "2026-10-28",
  "pending_payment_date": null,
  "reason": "Salary is delayed.",
  "expected_salary_date": "2026-10-27",
  "call_summary": "Intent: SALARY_DELAY; Reason: Salary is delayed.; Expected salary date: 2026-10-27; Promised payment date: 2026-10-28; Payment commitment status: CONFIRMED; Call status: COMPLETED",
  "sentiment": "POSITIVE_CUE",
  "stress_level": "UNKNOWN",
  "sentiment_score": 1
}
This example is illustrative. Actual output depends on the stored records and summary implementation.
Possible error responses (confirm exact status codes in /docs):
- 404 Not Found — call does not exist.
- 409 Conflict — required related record is missing, if handled this way.
- 422 Unprocessable Entity — invalid call ID.
- 503 Service Unavailable — summary persistence failure, if handled by the implementation.
Example End-to-End Workflow — Windows PowerShell
Run these commands from PowerShell while the API server is running. The examples use customer ID 2; make sure that customer exists and has an eligible EMI in your database. The workflow creates a new call record.
# 1. Check application health
curl.exe -X GET "http://127.0.0.1:8000/health"

# 2. Retrieve EMI details
curl.exe -X GET "http://127.0.0.1:8000/customers/2/emi"

# 3. Start a simulated call and capture its ID
$call = curl.exe -s -X POST "http://127.0.0.1:8000/customers/2/call" | ConvertFrom-Json
$callId = $call.call_id
Write-Host "Call ID: $callId"

# 4. Send a tentative date. It must not confirm a commitment.
$body1 = @{
    message = "My salary is delayed. I will try to pay on 28 October 2026, but I cannot guarantee it."
} | ConvertTo-Json -Compress
curl.exe -X POST "http://127.0.0.1:8000/calls/$callId/message" `
  -H "Content-Type: application/json" `
  -d $body1

# 5. Continue the conversation with the customer's actual response.
# Do not send an affirmative confirmation unless the agent has asked
# a date-specific confirmation question and the customer really confirms it.
$body2 = @{
    message = "I will receive my salary by 27 October and plan to pay on 28 October 2026."
} | ConvertTo-Json -Compress
curl.exe -X POST "http://127.0.0.1:8000/calls/$callId/message" `
  -H "Content-Type: application/json" `
  -d $body2

# 6. Request a summary
curl.exe -X POST "http://127.0.0.1:8000/calls/$callId/summary"
Conversation responses depend on the application's current state and AI output. Review each agent reply before sending the next customer message; do not blindly replay the example as though it were a guaranteed transcript.
Notes Before Submission
- Compare this document with app/api/, the request models, and the running /docs page. Update example schemas, status codes, and error messages to match the implementation exactly.
- Use fictional demonstration data only.
- Do not include .env, API keys, passwords, or other secrets in screenshots, logs, documentation, or a submission archive.
- Database-backed tests may write or delete records. Run them only after verifying that the configured database is safe for testing.
