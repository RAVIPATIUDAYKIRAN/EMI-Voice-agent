# System Architecture — AI EMI Collection Voice Agent

## 1. High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph ClientLayer["Client & Integration Layer"]
        Client["Client Application / Caller Interface"]
    end

    subgraph ConfigLayer["Configuration Layer"]
        AppSettings["Centralized Settings (app/config/settings.py)<br/>Pydantic Settings & Masked Secrets"]
    end

    subgraph FastAPILayer["FastAPI Application (ASGI)"]
        RouterMain["FastAPI App (app/main.py)"]
        HealthRouter["Health Check Router (/health)"]
        CustRouter["Customer & Commitment Router (/customers)"]
        CallRouter["Call Initiation Router (/customers/{id}/call)"]
        ConvRouter["Conversation & Summary Router (/calls)"]
        LocalVoiceRouter["Local WAV Router (/calls/{id}/voice-message)"]
        TelephonyRouter["Twilio Webhooks & Media WebSocket"]
        OperationsRouter["Dashboard & Callback Routes"]
    end

    subgraph ServiceLayer["Business Logic & AI Services"]
        ConfirmSvc["Payment Confirmation Engine<br/>(app/services/confirmation_service.py)"]
        ExtractSvc["Commitment & Intent Extraction<br/>(app/services/commitment_service.py)"]
        AISvc["Conversational Response Service<br/>(app/services/ai_service.py)"]
        SummarySvc["Call Summary Engine<br/>(app/api/conversations.py)"]
        SpeechSvc["OpenAI Whisper / TTS adapters"]
        LiveVoiceSvc["Bounded Twilio utterance bridge"]
    end

    subgraph ExternalAI["External LLM Provider"]
        OpenAIAPI["OpenAI API<br/>(Configured Model, Whisper, TTS)"]
    end

    subgraph ExternalVoice["Optional Voice Providers"]
        TwilioAPI["Twilio Voice & Media Streams"]
    end

    subgraph DataLayer["Persistence & Database Layer"]
        AsyncEngine["Async SQLAlchemy Engine (asyncmy)"]
        SessionMgr["AsyncSession & Row Locks (with_for_update)"]
        MySQL[(MySQL Database: emi_voice_agent)]

        subgraph Tables["Relational Schema"]
            T_Cust["customers"]
            T_EMI["emis"]
            T_Call["calls"]
            T_Msg["conversation_messages"]
            T_Callback["callback_schedules"]
        end
    end

    %% Client Interactions
    Client -->|HTTP GET/POST| RouterMain
    RouterMain --> HealthRouter
    RouterMain --> CustRouter
    RouterMain --> CallRouter
    RouterMain --> ConvRouter
    RouterMain --> LocalVoiceRouter
    RouterMain --> TelephonyRouter
    RouterMain --> OperationsRouter

    %% Router to Services
    CallRouter -->|Validate EMI & Init Call| SessionMgr
    ConvRouter -->|1. Validate Customer Reply| ConfirmSvc
    ConvRouter -->|2. Generate Polite Response| AISvc
    AISvc -->|Chat Completion| OpenAIAPI
    OpenAIAPI -->|Streamed/Text Reply| AISvc
    ConvRouter -->|3. Extract Intent & Dates| ExtractSvc
    ExtractSvc -->|Strict JSON Schema| OpenAIAPI
    OpenAIAPI -->|Structured Output| ExtractSvc
    ConvRouter -->|4. Build Call Summary| SummarySvc
    CustRouter -->|Validate Commitment| ConfirmSvc
    CustRouter -->|Record Commitment| SessionMgr
    LocalVoiceRouter -->|Validate WAV & transcribe| SpeechSvc
    SpeechSvc -->|Transcript through same handler| ConvRouter
    LocalVoiceRouter -->|Synthesize WAV response| SpeechSvc
    TwilioAPI -->|Signed HTTPS / WSS events| TelephonyRouter
    TelephonyRouter -->|Bounded μ-law utterances as WAV| SpeechSvc
    SpeechSvc -->|Whisper transcript| ConvRouter
    ConvRouter -->|Agent response text| LiveVoiceSvc
    LiveVoiceSvc -->|Raw μ-law 8 kHz playback| TwilioAPI
    LiveVoiceSvc --> SpeechSvc
    OperationsRouter -->|Authenticated reads / schedules| SessionMgr

    %% Configuration Distribution
    AppSettings -.->|Masked Credentials & Database URL| AsyncEngine
    AppSettings -.->|Model Name & Masked API Key| AISvc
    AppSettings -.->|OpenAI STT/TTS models & voice| SpeechSvc
    AppSettings -.->|Optional voice credentials| LiveVoiceSvc

    %% Database Operations
    ConvRouter -->|Atomic Writes & Rollback| SessionMgr
    SessionMgr --> AsyncEngine
    AsyncEngine --> MySQL
    MySQL --- Tables
```

WAV speech and Twilio voice are optional. WAV uploads use file-based Whisper; Twilio input is segmented on silence and each bounded segment is sent to Whisper. TTS WAV is converted to raw μ-law/8 kHz for Twilio playback. These are turn-based tests with mocked providers; no live audio or telephony provider run is claimed. Simulated text calls remain the default.

---

## 2. Core Architecture Principles

### A. Backend Authority Over Financial Commitments
- The Language Model (LLM) is strictly treated as an untrusted dialogue generator and natural language parser. It **cannot** directly update database records, finalize payment dates, or mark commitments confirmed.
- A payment promise is finalized **only** when the backend [confirmation_service.py](file:///c:/Users/udayk/Downloads/EMI-Voice-agent/app/services/confirmation_service.py) verifies that:
  1. An active `pending_payment_date` proposal exists on the call record, established through the conversational workflow (`POST /calls/{call_id}/message`).
  2. The actual stored latest agent message (`speaker == "AGENT"`) asked the customer to confirm that specific payment date.
  3. The customer returned an unambiguous affirmative response (`CONFIRMED`) matching the pending date.
  4. No tentative or ambiguous language (`"maybe"`, `"I will try"`, `"cannot guarantee"`) or conflicting payment dates were present.
- The commitment endpoint (`POST /customers/{customer_id}/commitment`) cannot create or replace a pending date proposal (rejecting unconfirmed date submissions with HTTP 409 Conflict), preventing arbitrary pending date injection outside the conversational workflow.

### B. Concurrency Control and Transaction Atomicity
- All state modifications (customer message insertion, agent reply insertion, pending proposal updates, confirmed date finalization, and call status transitions) occur within a single atomic SQLAlchemy `AsyncSession` transaction.
- Target call rows are locked using `with_for_update()` during message processing and commitment updates to prevent race conditions or contradictory state updates from concurrent API calls.
- If any service fails during a request (such as an LLM timeout or database constraint error), `db.rollback()` executes, leaving no orphaned messages or partial commitments.

### C. Sentence-Bounded Date & Intent Extraction
- The date extractor evaluates sentence boundaries (`.`, `!`, `?`, `;`, `\n`) and inspects question clauses strictly ending in `?`.
- Historical statements, EMI due dates (e.g., `"Your EMI of INR 8,500 was due on 10 October"`), and acknowledgment clauses (`"I understand that 25 October is tentative"`) are strictly separated from confirmation questions.
- A proposed date is stored as `pending_payment_date` only when the agent explicitly asks the customer to confirm that specific date.

---

## 3. Database Schema Overview

```mermaid
erDiagram
    customers ||--o{ emis : "has"
    customers ||--o{ calls : "participates in"
    emis ||--o{ calls : "collected via"
    calls ||--o{ conversation_messages : "contains"
    calls ||--o{ callback_schedules : "schedules"

    customers {
        int customer_id PK
        string full_name
        string phone_number
        string preferred_language
        datetime created_at
    }

    emis {
        int emi_id PK
        int customer_id FK
        decimal emi_amount
        date due_date
        string status "PENDING | OVERDUE | PAID"
        datetime created_at
    }

    calls {
        int call_id PK
        int customer_id FK
        int emi_id FK
        string call_status "INITIATED | COMPLETED | NO_ANSWER | FAILED"
        string reason
        date expected_salary_date
        date pending_payment_date
        date promised_payment_date
        boolean payment_commitment_confirmed
        text call_summary
        datetime started_at
        datetime ended_at
    }

    conversation_messages {
        bigint message_id PK
        int call_id FK
        string speaker "CUSTOMER | AGENT"
        text message_text
        datetime created_at
    }

    callback_schedules {
        int callback_id PK
        int call_id FK
        datetime scheduled_at
        int attempt_number
        string status
    }
```

---

## 4. Optional Voice, Callbacks, and Dashboard

- `POST /customers/{customer_id}/call?channel=voice` creates a persisted voice call, then requests Twilio. It requires Twilio/OpenAI configuration, public HTTPS/WSS, a valid allowlisted E.164 destination, and an `Idempotency-Key`. Simulated calls remain the default.
- Twilio webhooks are signature-checked. Recording and the media stream start only after explicit keypad consent. Bounded silence-delimited audio is transcribed with file-based Whisper; transcripts pass through the same message handler and backend confirmation checks as text messages. OpenAI TTS output is converted to Twilio's μ-law/8 kHz format. Recording media remains with Twilio; the app stores provider metadata and conversation transcripts.
- `POST /calls/{call_id}/callback` stores a timezone-aware callback request. The callback worker is opt-in; automatic retry scheduling is separately opt-in.
- `/dashboard` displays operational data only after `/dashboard/data` receives the configured `X-Dashboard-Key`; call data is joined through stored customer and EMI foreign keys.
- Sentiment and stress cues are lightweight lexicon-based indicators, not a clinical assessment.

## 5. Operational Boundaries and Scope

- **Simulation Mode:** Simulated text calls are the default. The optional Twilio + OpenAI path is turn-based, not live streaming STT. Live provider behavior requires deployment and account verification; playback marks and barge-in remain unsupported.
- **Model Boundaries:** The OpenAI client uses bounded retries with exponential backoff on transient errors (`408`, `409`, `429`, `5xx`), while failing fast without looping on permanent client errors (`401`, `403`, `404`).
