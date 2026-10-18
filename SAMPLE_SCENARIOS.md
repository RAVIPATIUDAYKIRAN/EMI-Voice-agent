Sample Scenarios — AI EMI Collection Voice Agent

> [!NOTE]
> **Simulation Notice**: The conversation transcripts and API payloads below are illustrative simulated demonstrations of the text-based voice-agent conversation workflow. Optional voice channels support OpenAI Whisper (`whisper-1`) and OpenAI TTS (`gpt-4o-mini-tts`) with Twilio Media Streams, but the core assignment evaluation is based on simulated conversation turns via the REST API or terminal CLI. Live provider delivery requires active external account credentials and verified destination numbers.

All scenarios must be verified against actual API responses and persisted database state. A recorded payment commitment represents the customer's stated intention; it does not constitute proof of payment or authorization of a bank transfer.

---

## Part 1: Three Representative Core Scenarios

### Scenario 1: Salary Delay and Expected Salary Date (Simulated Transcript)

**Context**: Customer 2 (Priya Reddy), EMI Amount ₹6,200.00. Current due date and status are read from the database.

#### Turn 1: Customer Reports Salary Delay Without Definite Date
- **Customer**: "My salary has been delayed by accounts. I will try to pay on 28 October 2026, but I cannot guarantee it right now."
- **Agent**: "I understand that 28 October is tentative and not guaranteed, Priya. Could you let us know when you expect your salary to be credited?"
- **Expected Backend State**:
  - `intent`: `SALARY_DELAY`
  - `reason`: `"Salary has been delayed by accounts"`
  - `expected_salary_date`: `null`
  - `pending_payment_date`: `null` (tentative dates are not accepted as firm proposals)
  - `promised_payment_date`: `null`
  - `payment_commitment_confirmed`: `false`
  - `commitment_status`: `"NOT_CONFIRMED"`
  - `call_status`: `"INITIATED"`

#### Turn 2: Customer Provides Expected Salary Date and Proposes Payment Date
- **Customer**: "HR confirmed my salary will be credited on 27 October 2026. I can make the EMI payment on 28 October 2026."
- **Agent**: "Thank you for the update. Would you be able to pay the INR 6,200 on 28 October 2026?"
- **Expected Backend State**:
  - `expected_salary_date`: `2026-10-27`
  - `pending_payment_date`: `2026-10-28` (agent asked date-specific confirmation question)
  - `promised_payment_date`: `null`
  - `payment_commitment_confirmed`: `false`
  - `commitment_status`: `"NOT_CONFIRMED"`
  - `call_status`: `"INITIATED"`

#### Turn 3: Customer Confirms Explicitly
- **Customer**: "Yes, I confirm that I will pay on 28 October 2026."
- **Agent**: "Thank you, Priya. Your payment commitment for 28 October 2026 has been confirmed. Have a great day!"
- **Expected Backend State**:
  - `pending_payment_date`: `null` (cleared upon confirmation)
  - `promised_payment_date`: `2026-10-28`
  - `payment_commitment_confirmed`: `true`
  - `commitment_status`: `"CONFIRMED"`
  - `call_status`: `"COMPLETED"`

#### Sanitized Schema-Compliant Call Summary Output (`POST /calls/{call_id}/summary`)
```json
{
  "call_id": 25,
  "customer_id": 2,
  "customer_name": "Priya Reddy",
  "emi_id": 2,
  "call_status": "COMPLETED",
  "payment_commitment_confirmed": true,
  "promised_payment_date": "2026-10-28",
  "pending_payment_date": null,
  "reason": "Salary has been delayed by accounts",
  "expected_salary_date": "2026-10-27",
  "call_summary": "Intent: SALARY_DELAY; Reason: Salary has been delayed by accounts; EMI amount: INR 6200.00; Customer sentiment: POSITIVE_CUE; stress cue: UNKNOWN; Expected salary date: 2026-10-27; Promised payment date: 2026-10-28; Payment commitment status: CONFIRMED; Call status: COMPLETED",
  "sentiment": "POSITIVE_CUE",
  "stress_level": "UNKNOWN",
  "sentiment_score": 1
}
```

---

### Scenario 2: Proposed Payment Date with Explicit Confirmation (Simulated Transcript)

**Context**: Customer 3 (Arjun Kumar, Hindi Preferred), EMI Amount ₹4,500.00.

#### Turn 1: Customer Proposes Specific Payment Date
- **Customer**: "नमस्ते, मैं 25 अक्टूबर 2026 को EMI का भुगतान करने का वादा करता हूँ।"
- **Agent**: "नमस्ते अर्जुन जी, क्या आप 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि कर सकते हैं?"
- **Expected Backend State**:
  - `intent`: `PROMISE_TO_PAY`
  - `pending_payment_date`: `2026-10-25` (persisted from agent confirmation question)
  - `promised_payment_date`: `null`
  - `payment_commitment_confirmed`: `false`
  - `commitment_status`: `"NOT_CONFIRMED"`
  - `call_status`: `"INITIATED"`

#### Turn 2: Customer Provides Explicit Hindi Affirmation
- **Customer**: "हाँ, मैं 25 अक्टूबर 2026 को ₹4500 की EMI का भुगतान करने की पुष्टि करता हूँ।"
- **Agent**: "धन्यवाद अर्जुन जी, आपकी 25 अक्टूबर 2026 की EMI भुगतान की पुष्टि दर्ज कर ली गई है। आपका दिन शुभ हो!"
- **Expected Backend State**:
  - `pending_payment_date`: `null`
  - `promised_payment_date`: `2026-10-25`
  - `payment_commitment_confirmed`: `true`
  - `commitment_status`: `"CONFIRMED"`
  - `call_status`: `"COMPLETED"`

#### Sanitized Schema-Compliant Call Summary Output (`POST /calls/{call_id}/summary`)
```json
{
  "call_id": 188,
  "customer_id": 3,
  "customer_name": "Arjun Kumar",
  "emi_id": 3,
  "call_status": "COMPLETED",
  "payment_commitment_confirmed": true,
  "promised_payment_date": "2026-10-25",
  "pending_payment_date": null,
  "reason": "Customer committed to pay on specified date",
  "expected_salary_date": null,
  "call_summary": "Intent: PROMISE_TO_PAY; Reason: Customer committed to pay on specified date; EMI amount: INR 4500.00; Customer sentiment: POSITIVE_CUE; stress cue: UNKNOWN; Promised payment date: 2026-10-25; Payment commitment status: CONFIRMED; Call status: COMPLETED",
  "sentiment": "POSITIVE_CUE",
  "stress_level": "UNKNOWN",
  "sentiment_score": 1
}
```

---

### Scenario 3: Inability to Pay or Refusal (Simulated Transcript)

**Context**: Customer 1 (Rahul Sharma), EMI Amount ₹8,500.00.

#### Turn 1: Customer Declines and States Inability to Pay
- **Customer**: "I lost my job last month and I have no money left. I cannot pay the EMI right now and I cannot commit to any date."
- **Agent**: "I understand, Rahul. Thank you for letting us know about your situation. We have noted this. Have a good day."
- **Expected Backend State**:
  - `intent`: `REFUSAL` or `UNABLE_TO_PAY`
  - `reason`: `"Customer lost job; unable to pay or commit"`
  - `expected_salary_date`: `null`
  - `pending_payment_date`: `null`
  - `promised_payment_date`: `null` (no fabricated commitment)
  - `payment_commitment_confirmed`: `false`
  - `commitment_status`: `"NOT_CONFIRMED"` (or `"DECLINED"`)
  - `call_status`: `"COMPLETED"` (polite call closure detected)

#### Sanitized Schema-Compliant Call Summary Output (`POST /calls/{call_id}/summary`)
```json
{
  "call_id": 12,
  "customer_id": 1,
  "customer_name": "Rahul Sharma",
  "emi_id": 1,
  "call_status": "COMPLETED",
  "payment_commitment_confirmed": false,
  "promised_payment_date": null,
  "pending_payment_date": null,
  "reason": "Customer lost job; unable to pay or commit",
  "expected_salary_date": null,
  "call_summary": "Intent: REFUSAL; Reason: Customer lost job; unable to pay or commit; EMI amount: INR 8500.00; Customer sentiment: NEGATIVE_CUE; stress cue: ELEVATED; Payment commitment status: NOT_CONFIRMED; Call status: COMPLETED",
  "sentiment": "NEGATIVE_CUE",
  "stress_level": "ELEVATED",
  "sentiment_score": -1
}
```

---

## Part 2: Additional Specialized Scenarios

### Scenario 4: Insufficient Funds (Multi-Turn or Immediate Close)
- **Customer**: "I don't have enough balance in my bank account this week."
- **Agent**: "I understand. Do you know approximately when you might have sufficient funds to clear the EMI?"
- **Outcome**: `payment_commitment_confirmed = false`, `promised_payment_date = null`. If the customer states they cannot commit, the agent politely concludes and completes the call without fabricating a commitment.

### Scenario 5: Customer States EMI Is Already Paid (`ALREADY_PAID`)
- **Customer**: "I already paid this EMI yesterday via UPI."
- **Agent**: "Thank you for letting us know, Rahul. We will verify your transaction against our bank records."
- **Outcome**: `payment_commitment_confirmed = false`, `promised_payment_date = null`. The EMI record is not marked paid until banking settlement verification.

### Scenario 6: Wrong Number or Unavailability
- **Customer**: "You have called the wrong person, I do not know any Rahul."
- **Agent**: "I apologize for the inconvenience. We will update our records. Goodbye."
- **Outcome**: `intent = WRONG_NUMBER`, `call_status = COMPLETED`, `payment_commitment_confirmed = false`.

### Scenario 7: Conflicting or Ambiguous Date Confirmation
- **Agent**: "Would you be able to pay on 25 October 2026?"
- **Customer**: "Maybe. I will see if my bonus arrives."
- **Outcome**: `ConfirmationStatus.AMBIGUOUS`. The pending date is not confirmed, `payment_commitment_confirmed = false`, `commitment_status = "NEEDS_CLARIFICATION"`.

---

## Verification Rules Checklist
1. All scenarios describe simulated text conversations executed through FastAPI REST endpoints or the interactive terminal CLI.
2. A vague, tentative, or conflicting statement never confirms a commitment.
3. An affirmative response confirms only the exact date asked about in the preceding date-specific agent question.
4. Final summaries match the database state and schema attributes.
