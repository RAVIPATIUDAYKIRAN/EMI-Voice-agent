from datetime import date

from app.services.confirmation_service import (
    ConfirmationStatus,
    verify_payment_confirmation,
)

PAYMENT_DATE = date(2026, 11, 7)
AGENT_QUESTION = (
    "Can you confirm that you can pay on 7 November 2026?"
)


def check(
    response: str,
    *,
    proposed_date: date | None = PAYMENT_DATE,
    pending_date: date | None = PAYMENT_DATE,
    agent_message: str = AGENT_QUESTION,
):
    return verify_payment_confirmation(
        customer_response=response,
        proposed_payment_date=proposed_date,
        agent_last_message=agent_message,
        pending_proposal_date=pending_date,
    )


def main():
    # 1. Explicit positive confirmations.
    for response in (
        "Yes",
        "Yes, I will pay",
        "Yes, I will pay on that date",
        "I confirm",
        "Confirmed",
        "I agree",
    ):
        result = check(response)
        assert result.status == ConfirmationStatus.CONFIRMED, (
            f"Expected confirmation for {response!r}, got {result.status}"
        )

    # 2. Tentative responses must not confirm payment.
    for response in (
        "I will try",
        "Maybe",
        "Hopefully",
        "I am not sure",
        "Let me see",
        "I will pay after salary",
        "Probably",
        "I cannot guarantee it",
    ):
        result = check(response)
        assert result.status == ConfirmationStatus.AMBIGUOUS, (
            f"Expected ambiguity for {response!r}, got {result.status}"
        )

    # 3. Explicit rejection must not confirm payment.
    for response in (
        "No",
        "I cannot",
        "I will not pay",
        "I refuse",
        "Not on that date",
    ):
        result = check(response)
        assert result.status == ConfirmationStatus.NOT_CONFIRMED, (
            f"Expected rejection for {response!r}, got {result.status}"
        )

    # 4. No pending proposal must never be confirmed.
    assert check("Yes", pending_date=None).status == (
        ConfirmationStatus.NO_PENDING_PROPOSAL
    )

    # 5. Conflicting proposed dates must not be confirmed.
    assert check(
        "Yes, I will pay",
        proposed_date=date(2026, 11, 8),
    ).status == ConfirmationStatus.NO_PENDING_PROPOSAL

    # 6. A different date stated by the customer must not be accepted.
    for response in (
        "Yes, I will pay on 8 November 2026",
        "I will pay on 2026-11-08",
    ):
        result = check(response)
        assert result.status != ConfirmationStatus.CONFIRMED, (
            f"Conflicting date was incorrectly confirmed: {response!r}"
        )

    # 7. The previous agent message must contain a valid confirmation question.
    for agent_message in (
        "",
        "Your EMI is INR 8500.",
        "Can you pay on 7 November?",  # No year.
    ):
        result = check("Yes, I will pay", agent_message=agent_message)
        assert result.status == ConfirmationStatus.NO_PENDING_PROPOSAL, (
            f"Invalid proposal question accepted: {agent_message!r}"
        )

    # 8. Invalid or empty customer responses must not confirm.
    for response in ("", "   "):
        result = check(response)
        assert result.status == ConfirmationStatus.AMBIGUOUS, (
            f"Invalid response accepted: {response!r}"
        )

    print("All confirmation service tests passed.")


def test_confirmation_service():
    main()


if __name__ == "__main__":
    main()
