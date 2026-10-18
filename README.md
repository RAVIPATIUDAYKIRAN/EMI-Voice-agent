AI EMI Collection Voice Agent
Overview
A Python backend MVP for an AI-powered EMI collection voice assistant. It simulates outbound call sessions, supports context-aware multi-turn conversations, identifies repayment intentions, applies backend confirmation logic to payment commitments, and stores call records and summaries in MySQL.
Operational scope: This is a text-based simulated voice agent. Live telephony, audio streaming, speech-to-text (STT), and text-to-speech (TTS) are not implemented in the current version.
Technology Stack
- Framework: FastAPI with Uvicorn
- Language: Python 3.11
- Database: MySQL 8.0+
- ORM/database access: SQLAlchemy 2.x with asyncmy
- AI/LLM: OpenAI API with a configurable model
- Validation: Pydantic
- Testing: Pytest, AnyIO, HTTPX
- Configuration: Centralized Pydantic Settings (app/config/settings.py) loaded from environment and .env
Project Structure
EMI-Voice-agent/
|-- app/
|   |-- api/
|   |   |-- calls.py
|   |   |-- conversations.py
|   |   `-- customers.py
|   |-- config/
|   |   |-- __init__.py
|   |   `-- settings.py
|   |-- db/
|   |   `-- database.py
|   |-- models/
|   |   |-- call.py
|   |   |-- conversation_message.py
|   |   |-- customer.py
|   |   `-- emi.py
|   |-- services/
|   |   |-- ai_service.py
|   |   |-- commitment_service.py
|   |   `-- confirmation_service.py
|   `-- main.py
|-- data/
|   |-- README.md
|   |-- schema.sql
|   `-- sample_data.sql
|-- docs/
|   `-- architecture.md
|-- tests/
|   |-- test_assignment_features.py
|   |-- test_ai_service.py
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
Environment Configuration
The application loads configuration from .env in the project root. Use the variable names expected by the application and replace these example values locally:
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_database_user
MYSQL_PASSWORD=your_database_password
MYSQL_DATABASE=emi_voice_agent

OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
Security rules:
- Never commit .env or share credentials in documentation, screenshots, logs, or source code.
- Keep secrets out of shared ZIP files and public repositories.
- Use a database account with only the permissions the application needs.
Database Setup
The application requires four MySQL tables: customers, emis, calls, and conversation_messages. It does not automatically create tables or insert sample records.
Files in data/:
- data/schema.sql — creates the required tables.
- data/sample_data.sql — inserts fictional demonstration records.
- data/README.md — setup instructions and safety precautions.
Setup order
1. Create or verify the dedicated assignment database.
2. Confirm MYSQL_DATABASE in .env points to the intended database.
3. Review existing data and back it up if needed.
4. Apply data/schema.sql using your MySQL client.
5. Check existing customer IDs, EMI IDs, and phone numbers before applying data/sample_data.sql.
6. Insert sample records only if they will not conflict with existing records.
7. Verify the tables and records before starting the application.
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
POST	/customers/{customer_id}/call	Initiate a simulated call session
POST	/calls/{call_id}/message	Process a conversation message
POST	/customers/{customer_id}/commitment	Record non-date details or finalize an existing pending payment commitment
POST	/calls/{call_id}/summary	Generate or retrieve a call summary


See API_DOCUMENTATION.md and the running Swagger UI for request schemas and response details.
Conversation and Commitment Confirmation Workflow
1. Call initiation: POST /customers/{customer_id}/call creates a simulated call for an eligible pending or overdue EMI.
2. Conversation: POST /calls/{call_id}/message processes each customer message and returns an agent response.
3. Intent/date extraction: The conversation logic handles repayment scenarios such as salary delay, insufficient funds, promise to pay, payment after salary, refusal, inability to pay, already paid, wrong number, unavailability, and unclear responses.
4. Confirmation safety:
   - Pending payment proposals are established solely through the conversation workflow (`POST /calls/{call_id}/message`), where the agent's date-specific confirmation question and pending date are committed in the same database transaction.
   - The commitment endpoint (`POST /customers/{customer_id}/commitment`) cannot create or replace a pending payment date; unconfirmed requests supplying a date return HTTP 409 Conflict.
   - Unconfirmed requests without a date can record permitted non-date details (`reason`, `expected_salary_date`) without modifying any existing pending date.
   - Final confirmation requires an active pending proposal date, a matching requested date, the actual stored latest agent message (`speaker == "AGENT"`), and explicit affirmative customer confirmation text validated by the confirmation service.
   - Ambiguous replies such as “I will try,” “maybe,” or “I cannot guarantee” are strictly rejected.
   - After valid confirmation, the confirmed date is stored, payment_commitment_confirmed is set to true, the pending date is cleared, and the call is completed.
   - An already confirmed commitment cannot be overwritten or reset by an unconfirmed request (HTTP 409).
5. Summary: POST /calls/{call_id}/summary generates or retrieves the call summary, according to the implemented workflow.
A recorded commitment represents the customer's stated intention. It does not verify receipt of funds or authorize a bank debit.
Demonstration Scenarios
See SAMPLE_SCENARIOS.md for illustrative transcripts. Use fictional customer data and verify actual scenario coverage against the tests before claiming that every scenario is fully tested.
How to Run Automated Tests
Run commands from the project root.
Isolated unit tests (safe; no database connection required):
.\.venv\Scripts\python.exe -m pytest -v tests/test_commitment_security.py tests/test_confirmation_service.py
Complete test suite (requires dedicated test database):
.\.venv\Scripts\python.exe -m pytest -v
Focused integration suites:
.\.venv\Scripts\python.exe -m pytest -v tests/test_assignment_features.py
.\.venv\Scripts\python.exe -m pytest -v tests/test_pending_date_bug_regression.py
Database connectivity test:
.\.venv\Scripts\python.exe -m pytest -v tests/test_db_connection.py
Before running database-backed tests: Verify MYSQL_DATABASE points to a dedicated test or assignment database and that existing data is safe. Some tests create call records and delete the specific records they created. Do not run these tests against production or unrelated data.
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
Architecture and Additional Documentation
- ARCHITECTURE.md — architecture overview and data-flow diagrams.
- docs/architecture.md — supplementary architecture documentation.
- API_DOCUMENTATION.md — API reference.
- SAMPLE_SCENARIOS.md — illustrative customer conversation scenarios.
- data/README.md — database setup instructions.
Compare the two architecture documents before removing any duplication.
Limitations
- Simulated voice environment: Live telephony, SIP/WebRTC audio streaming, and production call delivery are not included.
- No live STT/TTS pipeline: Speech recognition and voice synthesis are outside the current text-based implementation.
- Informational commitments: A confirmed customer statement is not evidence that the EMI has been paid.
- Database prerequisites: Tables and sample records must be prepared separately.
- External dependencies: LLM-backed features depend on API credentials, network access, and model availability.
- Production readiness: Deployment hardening, monitoring, privacy controls, load testing, and telephony integration require further validation before production use.