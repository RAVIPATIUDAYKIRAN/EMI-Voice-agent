AI EMI Collection Voice Agent
Overview
A Python backend MVP for an AI-powered EMI collection voice assistant. It simulates outbound call sessions, supports context-aware multi-turn conversations, identifies repayment intentions, applies backend confirmation logic to payment commitments, and stores call records and summaries in MySQL.
Operational scope: Simulated text calls work without voice-provider credentials. Optional voice uses OpenAI Responses for the configured LLM, Whisper file transcription, OpenAI TTS, and Twilio bidirectional Media Streams. Whisper processes bounded utterances after silence detection; it is not a native live-stream STT transport. No Twilio or audio-provider call has been made. A previous opt-in integration test called the configured LLM using an in-memory DB session; that historical request may incur provider usage charges.
Technology Stack
- Framework: FastAPI with Uvicorn
- Language: Python 3.11
- Database: MySQL 8.0+
- ORM/database access: SQLAlchemy 2.x with asyncmy
- AI/LLM: OpenAI API with a configurable model
- Optional speech: OpenAI Whisper (`whisper-1`) and TTS (`gpt-4o-mini-tts`)
- Optional telephony: Twilio Programmable Voice and bidirectional Media Streams
- Validation: Pydantic
- Testing: Pytest, AnyIO, HTTPX
- Configuration: Centralized Pydantic Settings (app/config/settings.py) loaded from environment and .env
Project Structure
EMI-Voice-agent/
|-- app/
|   |-- api/
|   |   |-- calls.py
|   |   |-- callbacks.py
|   |   |-- conversations.py
|   |   |-- customers.py
|   |   |-- dashboard.py
|   |   `-- telephony.py
|   |-- config/
|   |   |-- __init__.py
|   |   `-- settings.py
|   |-- db/
|   |   `-- database.py
|   |-- models/
|   |   |-- call.py
|   |   |-- callback_schedule.py
|   |   |-- conversation_message.py
|   |   |-- customer.py
|   |   `-- emi.py
|   |-- services/
|   |   |-- ai_service.py
|   |   |-- callback_service.py
|   |   |-- commitment_service.py
|   |   |-- confirmation_service.py
|   |   |-- sentiment_service.py
|   |   `-- voice_service.py
|   `-- main.py
|-- data/
|   |-- README.md
|   |-- schema.sql
|   `-- sample_data.sql
|-- docs/
|   `-- architecture.md
|-- tests/
|   |-- test_bonus_features.py
|   |-- test_ai_service.py
|   |-- test_hindi_commitment_flow.py
|   |-- test_commitment_security.py
|   |-- test_commitment_service.py
|   |-- test_confirmation_service.py
|   |-- test_db_connection.py
|   `-- test_pending_date_bug_regression.py
|-- API_DOCUMENTATION.md
|-- ARCHITECTURE.md
|-- SAMPLE_SCENARIOS.md
|-- requirements.txt
|-- .gitignore
`-- README.md
Verify the tree against the actual directory before submission. .env is intentionally omitted because it contains local configuration and secrets.
Prerequisites
- Python 3.11
- MySQL Server 8.0+ running locally or accessible over the network
- Dependencies installed in .venv
- An OpenAI API key if the configured LLM integration is used
- A dedicated assignment database
Windows PowerShell setup and run:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, check health and open Swagger:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Start-Process 'http://127.0.0.1:8000/docs'
```

For public Twilio webhooks, run `ngrok http 8000`, set `PUBLIC_BASE_URL` to its HTTPS forwarding URL, and restart FastAPI. This does not place a call.

### Interactive terminal conversation (simulated)

Run this from the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_terminal_conversation.py
```

Enter an existing customer ID when prompted. The terminal creates a simulated call using the normal customer/EMI selection route, generates and stores the opening agent message, and sends each typed response through the existing conversation handler. Type `exit` at a customer prompt to stop. The existing LLM configuration and database are used; no Twilio call is made and Twilio credentials are not required. A call row and conversation messages are persisted normally, and a commitment can be saved only when the existing confirmation checks accept the customer's explicit confirmation. Set `OPENAI_API_KEY` before starting; language follows the customer's stored preference.
### Run FastAPI and the terminal conversation together

To keep Swagger available while you interact with the customer conversation in the same PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_app_and_conversation.py
```

The launcher starts Uvicorn as a child process, shows its logs in the terminal, waits for `/health`, and then starts the existing simulated conversation CLI with interactive keyboard input. Open `http://localhost:8000/docs` in a browser to use Swagger while the terminal conversation continues. Type `exit` to end the conversation; press Ctrl+C to stop the workflow. The launcher then shuts down the Uvicorn child. No Twilio call is made. The standalone CLI command above and the normal Uvicorn startup command below remain available.

Environment Configuration
The application loads configuration from .env in the project root. Use the variable names expected by the application and replace these example values locally:
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_database_user
MYSQL_PASSWORD=your_database_password
MYSQL_DATABASE=emi_voice_agent

OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-6-luna
OPENAI_STT_MODEL=whisper-1
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=coral

# Optional real voice integrations
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_FROM_NUMBER=replace_with_assigned_twilio_voice_number
PUBLIC_BASE_URL=https://your-ngrok-url.ngrok-free.dev
TWILIO_ALLOWED_DESTINATIONS=replace_with_authorized_test_number

# Bounded WAV uploads and buffered telephony audio
LOCAL_AUDIO_MAX_BYTES=10000000
LOCAL_AUDIO_MAX_SECONDS=120
DASHBOARD_API_KEY=use-a-long-random-secret
CALLBACK_WORKER_ENABLED=false
VOICE_AUTO_RETRY_ENABLED=false
Security rules:
- Never commit .env or share credentials in documentation, screenshots, logs, or source code.
- Keep secrets out of shared ZIP files and public repositories.
- Use a database account with only the permissions the application needs.
Database Setup
The application requires customers, emis, calls, conversation_messages, and callback_schedules tables. It does not automatically create tables or insert sample records.
Files in data/:
- data/schema.sql — creates the required tables.
- data/migrations/001_callback_schedules.sql — adds durable callback scheduling to an existing installation.
- data/sample_data.sql — inserts fictional demonstration records.
- data/README.md — setup instructions and safety precautions.
Setup order
1. Create or verify the dedicated assignment database.
2. Confirm MYSQL_DATABASE in .env points to the intended database.
3. Review existing data and back it up if needed.
4. Apply data/schema.sql using your MySQL client.
5. For an existing database, apply data/migrations/001_callback_schedules.sql before using callback scheduling.
6. Check existing customer IDs, EMI IDs, and phone numbers before applying data/sample_data.sql.
7. Insert sample records only if they will not conflict with existing records.
8. Verify the tables and records before starting the application.
Database safety:
- Never run these scripts against production or an unrelated database.
- CREATE TABLE IF NOT EXISTS does not migrate or update existing table definitions.
- The sample-data script is not idempotent and can conflict with existing IDs or phone numbers.
- Creating the SQL files does not execute them.
- Database-backed tests may create or delete test records; run them only against a database safe for testing.
See data/README.md for details.
How to Start the Application
Open Windows PowerShell in the project root.
Activate the virtual environment:
.\.venv\Scripts\Activate.ps1
If the virtual environment does not exist yet:
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Start the application:
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
When the server is running:
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
API Endpoints
Method	Endpoint	Purpose
GET	/health	Check application health
GET	/health/database	Check MySQL connectivity
GET	/customers/{customer_id}/emi	Retrieve customer and EMI information
POST	/customers/{customer_id}/call	Initiate a simulated call or optional Twilio voice call (?channel=voice)
POST	/calls/{call_id}/message	Process a conversation message
POST	/customers/{customer_id}/commitment	Record non-date details or finalize an existing pending payment commitment
POST	/calls/{call_id}/summary	Generate or retrieve a call summary
POST	/calls/{call_id}/callback	Schedule an authenticated callback
GET	/dashboard	Open the operations dashboard
GET	/dashboard/data	Read authenticated call and commitment metrics


## Demonstrations

Screenshots of the simulated API workflows are available in
[`docs/demonstrations/`](docs/demonstrations/).

The demonstrations cover:
- FastAPI Swagger UI and available endpoints
- English simulated conversation and call summary
- Hindi simulated conversation and call summary

These are simulated conversations, not evidence of completed live telephone calls.


See API_DOCUMENTATION.md and the running Swagger UI for request schemas and response details.
Conversation and Commitment Confirmation Workflow
1. Call initiation: POST /customers/{customer_id}/call creates a simulated call by default. Add ?channel=voice to explicitly place a real Twilio call when providers are configured.
2. Conversation: POST /calls/{call_id}/message processes each customer message and returns an agent response.
3. Intent/date extraction: The conversation logic handles repayment scenarios such as salary delay, insufficient funds, promise to pay, payment after salary, refusal, inability to pay, already paid, wrong number, unavailability, and unclear responses.
   - The customer's saved preferred language is passed to the response generator; Hindi and English date/confirmation parsing are handled by the same backend validation service. Optional voice consent and opening prompts are also localized for Hindi and English.
4. Confirmation safety:
   - Pending payment proposals are established solely through the conversation workflow (`POST /calls/{call_id}/message`), where the agent's date-specific confirmation question and pending date are committed in the same database transaction.
   - The commitment endpoint (`POST /customers/{customer_id}/commitment`) cannot create or replace a pending payment date; unconfirmed requests supplying a date return HTTP 409 Conflict.
   - Unconfirmed requests without a date can record permitted non-date details (`reason`, `expected_salary_date`) without modifying any existing pending date.
   - Final confirmation requires an active pending proposal date, a matching requested date, the actual stored latest agent message (`speaker == "AGENT"`), and explicit affirmative customer confirmation text validated by the confirmation service.
   - Ambiguous replies such as “I will try,” “maybe,” or “I cannot guarantee” are strictly rejected.
   - After valid confirmation, the confirmed date is stored, payment_commitment_confirmed is set to true, the pending date is cleared, and the call is completed.
   - An already confirmed commitment cannot be overwritten or reset by an unconfirmed request (HTTP 409).
5. Summary: POST /calls/{call_id}/summary generates or retrieves the call summary, according to the implemented workflow.
6. Voice mode requires explicit recording consent before recording starts; transcripts are stored as customer conversation messages.
7. Dashboard and callback scheduling require the X-Dashboard-Key header and DASHBOARD_API_KEY. Callback dispatch runs only when CALLBACK_WORKER_ENABLED=true; automatic retries also require VOICE_AUTO_RETRY_ENABLED=true.
8. Optional assessment features include consent-gated recording/transcription, durable callback scheduling and retry hooks, an authenticated read-only dashboard, and bilingual lexicon-based sentiment/stress cues.
A recorded commitment represents the customer's stated intention. It does not verify receipt of funds or authorize a bank debit.
Demonstration Scenarios
See SAMPLE_SCENARIOS.md for illustrative transcripts. Use fictional customer data and verify actual scenario coverage against the tests before claiming that every scenario is fully tested.
Reproducible Hindi/English Swagger walkthroughs and the optional audio limitation are documented in docs/demo_scenarios.md. Requirement-by-requirement evidence and gaps are tracked in docs/requirements_audit.md.
How to Run Automated Tests
Run commands from the project root.
Isolated tests (safe; no configured database writes):
.\.venv\Scripts\python.exe -m pytest -v tests/test_bonus_features.py tests/test_local_voice.py tests/test_hindi_commitment_flow.py tests/test_mapping_regressions.py tests/test_commitment_security.py tests/test_confirmation_service.py tests/test_commitment_service.py tests/test_ai_service.py tests/test_configuration.py
Complete safe suite (excludes legacy modules that connect to or write/delete from configured MySQL):
\.venv\Scripts\python.exe -m pytest tests -q -rs --ignore=tests/test_assignment_features.py --ignore=tests/test_pending_date_bug_regression.py --ignore=tests/test_db_connection.py
Unfiltered test suite (requires an isolated dedicated test database; some legacy tests write/delete records):
.\.venv\Scripts\python.exe -m pytest -v
Focused integration suites:
.\.venv\Scripts\python.exe -m pytest -v tests/test_assignment_features.py
.\.venv\Scripts\python.exe -m pytest -v tests/test_pending_date_bug_regression.py
Database connectivity test:
.\.venv\Scripts\python.exe -m pytest -v tests/test_db_connection.py
Before running database-backed tests: Verify MYSQL_DATABASE points to a dedicated test or assignment database and that existing data is safe. Some tests create call records and delete the specific records they created. Do not run these tests against production or unrelated data.
Manual authorized voice test: Do not place a live call without explicit user authorization. Review and apply the schema migrations to the intended development database, configure Twilio/OpenAI credentials, allowlist only the specifically authorized destination in `TWILIO_ALLOWED_DESTINATIONS`, expose the app over HTTPS, and confirm that the customer record uses that number. In Swagger, submit `POST /customers/{customer_id}/call?channel=voice` with a fresh UUID-like `Idempotency-Key`. Retrying the same key returns the existing call without a second provider request. Recording and transcription begin only after keypad consent.
Report actual pass/fail counts from the current test run; do not rely on an old count.
Security and Data Integrity
Review the implementation against these principles:
1. Backend-controlled confirmation: The LLM must not independently authorize or finalize a payment commitment.
2. Input validation: Validate request data and reject invalid values as required by the API schemas.
3. Database access: Prefer parameterized ORM operations over SQL constructed from untrusted input.
4. Transaction integrity: Use appropriate transaction boundaries and concurrency controls for commitment updates.
5. Credential protection: Load secrets from environment variables and exclude .env from version control.
6. Error handling: Do not return internal exception details, credentials, or unnecessary customer information to API clients.
7. Data minimization: Use fictional demonstration data and avoid exposing customer information in logs or documentation.
These are review principles, not blanket guarantees. Confirm each implementation detail in the relevant source code before claiming a specific security control is fully implemented.
## Documentation Index

| Document | Purpose & Contents |
| :--- | :--- |
| [README.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/README.md) | Project overview, technology stack, setup guide, startup commands, testing instructions, and deliverables checklist |
| [API_DOCUMENTATION.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/API_DOCUMENTATION.md) | Complete REST API endpoint inventory, request/response models, auth headers, and end-to-end PowerShell workflows |
| [ARCHITECTURE.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/ARCHITECTURE.md) | Primary system architecture, Mermaid data-flow diagram, concurrency model, and database entity relationships |
| [docs/architecture.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/docs/architecture.md) | Architectural deep-dive, component boundaries, and security model |
| [SAMPLE_SCENARIOS.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/SAMPLE_SCENARIOS.md) | Three representative simulated conversation scenarios + secondary scenarios with sanitized `CallSummaryResponse` schemas |
| [docs/demo_scenarios.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/docs/demo_scenarios.md) | Step-by-step reproducible Swagger UI walkthroughs for English and Hindi collection calls |
| [docs/requirements_audit.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/docs/requirements_audit.md) | Requirement-by-requirement verification audit matrix, evidence, and limitation analysis |
| [data/README.md](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/data/README.md) | Database initialization safety, migration instructions, and sample dataset notes |

---

## Deliverables Checklist and Audit Status

| # | Assignment Deliverable | Status | Primary Implementation / Artifact |
| :- | :--- | :--- | :--- |
| 1 | **FastAPI Application Backend** | Verified Complete | `app/main.py`, `app/api/` (customers, calls, conversations, dashboard, telephony) |
| 2 | **Relational Database Schema & Data** | Verified Complete | `data/schema.sql`, `data/sample_data.sql` (fictional demonstration records) |
| 3 | **Context-Aware Conversational AI** | Verified Complete | `app/api/conversations.py`, `app/services/ai_service.py` (multi-turn memory) |
| 4 | **Intent & Entity/Date Extraction** | Verified Complete | `app/services/commitment_service.py`, `app/services/confirmation_service.py` |
| 5 | **Backend-Enforced Commitment Security** | Verified Complete | `app/services/confirmation_service.py` (explicit question-reply matching; rejects tentative proposals) |
| 6 | **API Documentation & Interactive UI** | Verified Complete | `API_DOCUMENTATION.md`, Swagger UI (`/docs`), ReDoc (`/redoc`) |
| 7 | **Architecture & Flow Diagrams** | Verified Complete | `ARCHITECTURE.md`, `docs/architecture.md` (native Mermaid diagrams) |
| 8 | **Sample Scenarios & Call Summaries** | Verified Complete | `SAMPLE_SCENARIOS.md` (3 core scenarios + secondary; sanitized JSON outputs) |
| 9 | **Centralized Configuration** | Verified Complete | `app/config/settings.py` (working-directory-independent Pydantic settings) |
| 10 | **Automated Test Suite** | Verified Complete | `tests/` () |

---

## Limitations

- **Real voice mode** requires OpenAI/Twilio credentials, a public HTTPS/WSS URL, an allowlisted E.164 destination, and an authorized `Idempotency-Key`. Calls may incur provider charges. No live provider operation was performed.
- **Twilio Voice & Audio**: Twilio receives bounded speech segments,Automated tests are available in tests/. See docs/requirements_audit.md for dated test results, verification scope, and known limitations.transcribed using file-based Whisper, and uses OpenAI TTS output converted to raw μ-law/8 kHz frames. This is turn-based, not real-time streaming STT. Playback `mark` acknowledgements and barge-in are not implemented or provider-verified.
- **Local Audio (`POST /calls/{call_id}/voice-message`)**: Accepts bounded PCM WAV, calls OpenAI STT, delegates to the regular conversation handler, and returns OpenAI TTS WAV as base64 JSON. OpenAI credentials are required only when voice routes are invoked. TTS voices are optimized for English; Hindi speech quality has not been verified.
- **Callback Scheduling**: Requires the supplied SQL migration (`data/migrations/001_callback_schedules.sql`) and an explicitly enabled worker (`CALLBACK_WORKER_ENABLED=true`).
- **Dashboard Security**: `POST /calls/{call_id}/callback` and dashboard data return HTTP 503 when `DASHBOARD_API_KEY` is absent or shorter than 32 characters; they return 401 for a missing or incorrect request key after configuration. Set a randomly generated 32+ character secret in the process environment or local `.env`. This is fail-closed behavior.
- **Voice Consent**: Recording and transcription begin only after the recipient presses 1 to consent.
- **Provider Mocking**: Provider behavior is mocked in tests; live account delivery has not been verified.
- **Informational Commitments**: A confirmed customer statement is not evidence that the EMI has been paid.
- **Database Prerequisites**: Tables and sample records must be prepared separately; SQL files are not auto-executed.
- **External Dependencies**: LLM-backed features depend on API credentials, network access, and model availability.
- **Production Readiness**: Deployment hardening, monitoring, privacy controls, load testing, and telephony integration require further validation before production use.
