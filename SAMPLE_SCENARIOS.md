Sample Scenarios — AI EMI Collection Voice Agent
Notice: These are illustrative scenarios for the simulated, text-based MVP. They describe expected conversation behavior and possible state transitions; they are not proof that every path has been tested. Live telephony, speech-to-text (STT), and text-to-speech (TTS) are not integrated.

Use the actual API response and persisted database state to verify each scenario. Intent labels and state transitions must match the implementation before being presented as verified behavior.
Complete End-to-End Conversation Examples
Scenario 1: Salary Delay Followed by Confirmed Payment Date
Illustrative context: Customer 2 (Priya Reddy), EMI amount ₹6,200.00. The EMI due date and current status must be read from the database because they can change.
Turn 1
Customer: My salary is delayed. I will try to pay on 28 October 2026, but I cannot guarantee it right now.
Agent: I understand that 28 October is tentative and not guaranteed. Please let us know when you have a clearer idea of the payment timing.
Expected safety outcome after Turn 1
- Intent may be SALARY_DELAY.
- A payment date may be extracted as a tentative fact, depending on the extractor.
- payment_commitment_confirmed remains 0.
- promised_payment_date remains NULL.
- Any stale pending proposal should be cleared when the customer explicitly makes the date tentative, according to the implemented confirmation rules.
- The call need not be completed yet.
Turn 2
Customer: HR confirmed that my salary will be credited on 27 October. I plan to pay on 28 October.
Agent: Thank you for the update. Would you be able to confirm that you will make the payment of INR 6,200 on 28 October 2026?
Expected safety outcome after Turn 2
- expected_salary_date may be 2026-10-27.
- promised_payment_date remains NULL.
- payment_commitment_confirmed remains 0.
- The proposed payment date may be stored as pending_payment_date = 2026-10-28 if the application has asked a date-specific confirmation question and its logic records that proposal at this stage.
- The call remains eligible for the customer's response.
Turn 3
Customer: Yes, I will pay on 28 October 2026.
Agent: Thank you. Your payment commitment for 28 October 2026 has been confirmed. Have a pleasant day!
Expected safety outcome after Turn 3, if the backend accepts the reply as confirmation of the immediately preceding date-specific question
- pending_payment_date is cleared (NULL).
- promised_payment_date = 2026-10-28.
- payment_commitment_confirmed = 1.
- call_status = COMPLETED.
- A summary reflects the stored reason, salary date, confirmed payment date, and call status.
Illustrative summary:
Intent: SALARY_DELAY; Reason: Salary is delayed.; Expected salary date: 2026-10-27; Promised payment date: 2026-10-28; Payment commitment status: CONFIRMED; Call status: COMPLETED
Scenario 2: Insufficient Funds Without a Confirmed Commitment
Illustrative context: Customer 1 (Rahul Sharma), EMI amount ₹8,500.00. Read the current due date and status from the database.
Turn 1
Customer: I don't have enough money in my account right now because of unexpected expenses.
Agent: I understand that unexpected expenses have made things difficult. Do you have an estimate of when funds might be available for the EMI payment?

Turn 2
Customer: I really cannot say for sure. Maybe towards the end of next month.
Agent: I understand that the timing is uncertain. Thank you for letting us know. Please contact us when you have a clearer idea of the payment timing.
Expected safety outcome
- The reason/intent may indicate insufficient funds.
- No exact payment date should be treated as confirmed from “maybe towards the end of next month.”
- payment_commitment_confirmed = 0.
- promised_payment_date = NULL.
- pending_payment_date should remain NULL if the date is not accepted as a concrete proposal.
- The final call status depends on the implemented flow; verify it from the API response rather than assuming it remains INITIATED or becomes COMPLETED.
Illustrative summary (wording depends on the summary implementation):
Intent: INSUFFICIENT_FUNDS; Payment commitment status: NOT_CONFIRMED
Scenario 3: Direct Promise to Pay Followed by Explicit Confirmation
Illustrative context: Customer 3 (Arjun Kumar), EMI amount ₹4,500.00. Verify the due date and status from the database before using this example.
Turn 1
Customer: I will pay my EMI on 5 November 2026.
Agent: Thank you. Would you be able to confirm that you will make the payment of INR 4,500 on 5 November 2026?

Turn 2
Customer: Yes, I confirm that I will pay on 5 November 2026.
Agent: Thank you. Your payment commitment for 5 November 2026 has been confirmed. Have a great day!
Expected safety outcome
- After Turn 1, pending_payment_date may be 2026-11-05; promised_payment_date remains NULL; payment_commitment_confirmed = 0.
- The affirmative response in Turn 2 can confirm the date only if the preceding agent turn actually asked a date-specific confirmation question for that same date.
- After valid confirmation, pending_payment_date = NULL, promised_payment_date = 2026-11-05, payment_commitment_confirmed = 1, and call_status = COMPLETED.
Concise Illustrative Scenarios
Scenario 4: Payment After Salary (AFTER_SALARY)
Customer: “I will pay as soon as my salary arrives.”
Possible agent response: “Thank you for letting us know. When do you expect your salary to be credited?”
Expected safety outcome
- No exact salary or payment date is available yet.
- expected_salary_date = NULL.
- promised_payment_date = NULL.
- payment_commitment_confirmed = 0.
- pending_payment_date = NULL unless a concrete date is subsequently proposed and recorded by the application.
- Verify the exact intent label used by the implementation; do not assume AFTER_SALARY is a supported enum unless the code defines it.
Scenario 5: Refusal or Inability to Pay
Customer: “I cannot pay right now.”
Possible agent response: “I understand. Thank you for letting us know about your current situation.”
Expected safety outcome
- The message should not create a payment commitment.
- payment_commitment_confirmed = 0.
- promised_payment_date = NULL.
- pending_payment_date = NULL.
- Verify whether the implementation labels this REFUSAL, UNABLE_TO_PAY, or another intent. Do not claim both labels are emitted unless confirmed by the code.
Scenario 6: Customer Says the EMI Is Already Paid (ALREADY_PAID)
Customer: “I already paid this EMI yesterday.”
Possible agent response: “Thank you for informing us. Your payment claim will need to be checked against the payment records.”
Expected safety outcome
- Do not create a new payment commitment.
- payment_commitment_confirmed = 0.
- promised_payment_date = NULL.
- pending_payment_date = NULL.
- Do not mark the EMI as paid solely because the customer says it was paid. An authorized payment-record verification process must establish the actual status.
Scenario 7: Wrong Number (WRONG_NUMBER)
Customer: “You have reached the wrong person.”
Possible agent response: “I apologize for the disturbance. Thank you for letting us know. Goodbye.”
Expected safety outcome
- Do not collect or confirm a payment commitment.
- payment_commitment_confirmed = 0.
- promised_payment_date = NULL.
- pending_payment_date = NULL.
- The call may be marked COMPLETED if the implemented wrong-number flow closes the session. Verify the persisted status.
Scenario 8: Customer Unavailable
Trigger: In a text simulation, this must be represented by an explicit simulated input or application event; it is not detected from live telephony in the current MVP.
Possible agent response: “I understand this is not a convenient time. Thank you.”
Expected safety outcome
- No payment commitment is created.
- payment_commitment_confirmed = 0.
- promised_payment_date = NULL.
- Verify whether the implementation completes the call or leaves it in another state. Do not claim a NO_ANSWER state unless that status is implemented.
Scenario 9: Ambiguous Response to a Confirmation Question
Agent: “Can you confirm that you will pay on 25 October 2026?”
Customer: “Maybe. I will see when my salary comes.”
Possible agent response: “I understand that the date is uncertain. Please let us know when you have a clearer timeline.”
Expected safety outcome
- Do not treat the reply as confirmation.
- payment_commitment_confirmed = 0.
- promised_payment_date = NULL.
- Clear the pending proposal if the implementation's ambiguity-handling rule requires it.
- Verify the actual intent and call-status labels returned by the API.
Scenario 10: Customer Proposes a Different Date
Agent: “Can you confirm that you will pay on 25 October 2026?”
Customer: “No, I will pay on 10 November 2026 instead.”
Possible agent response: “Understood. Would you be able to confirm payment on 10 November 2026?”
Expected safety outcome
- The original date is not confirmed.
- payment_commitment_confirmed = 0 until the customer explicitly confirms the new date after the relevant date-specific question.
- promised_payment_date = NULL while the new date is pending.
- pending_payment_date = 2026-11-10 only if the application records the new date as a pending proposal.
- Do not mark the call completed or finalize the new date before valid confirmation.
Verification Checklist
Before describing a scenario as implemented and tested:
1. Run it through the actual API using a safe assignment/test database.
2. Inspect the API response after each turn.
3. Verify pending_payment_date, promised_payment_date, payment_commitment_confirmed, and call_status in persisted state.
4. Confirm that a vague or tentative response never confirms a commitment.
5. Confirm that an affirmative response only confirms the exact date asked about in the preceding agent confirmation question.
6. Verify the final summary matches the persisted state.
7. Use the actual intent/status labels returned by the implementation; the labels in these examples are descriptive unless verified in code.
Important: A confirmed commitment records the customer's stated intention. It does not establish that the EMI has been paid.