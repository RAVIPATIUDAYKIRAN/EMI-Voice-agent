
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class ConfirmationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_PENDING_PROPOSAL = "NO_PENDING_PROPOSAL"


@dataclass(frozen=True)
class ConfirmationResult:
    status: ConfirmationStatus
    payment_date: date | None
    reason: str


_AMBIGUOUS_PATTERNS = (
    r"\bmaybe\b",
    r"\bi(?:'ll| will) try\b",
    r"\bprobably\b",
    r"\bhopefully\b",
    r"\bnot sure\b",
    r"\bi think\b",
    r"\blet me see\b",
    r"\bi will see\b",
    r"\bi cannot guarantee\b",
    r"\bi can't guarantee\b",
    r"\bafter (?:my )?salary\b",
    r"\bwhen (?:my )?salary comes\b",
    r"\bi hope\b",
)

_NEGATIVE_PATTERNS = (
    r"\bno\b",
    r"\bi cannot\b",
    r"\bi can't\b",
    r"\bi will not\b",
    r"\bi won't\b",
    r"\bi refuse\b",
    r"\bi cannot confirm\b",
    r"\bi can't confirm\b",
    r"\bnot on that date\b",
    r"\bnot that date\b",
)

_EXPLICIT_YES_PATTERNS = (
    r"yes",
    r"yes i will",
    r"yes i can",
    r"yes i will pay",
    r"yes i can pay",
    r"yes i will pay on that date",
    r"yes i can pay on that date",
    r"i confirm",
    r"i confirm the payment",
    r"confirmed",
    r"i agree",
    r"sure i will pay",
    r"okay i will pay",
    r"ok i will pay",
    r"i will pay",
    r"i can pay",
)


def _normalize(value: str) -> str:
    value = value.lower().replace("’", "'")
    value = re.sub(r"[^\w\s']", " ", value)
    return " ".join(value.split())




def _date_is_in_message(message: str, payment_date: date) -> bool:
    """Require a full date, or a yearless date in explicit confirmation wording."""
    normalized = message.lower()

    # Full ISO date.
    if payment_date.isoformat() in normalized:
        return True

    day = payment_date.day
    year = payment_date.year
    month = payment_date.strftime("%B").lower()
    short_month = payment_date.strftime("%b").lower()

    full_date_patterns = (
        rf"\b{day}(?:st|nd|rd|th)?\s+{month}\s+{year}\b",
        rf"\b{day}(?:st|nd|rd|th)?\s+{short_month}\s+{year}\b",
        rf"\b{month}\s+{day}(?:st|nd|rd|th)?,?\s+{year}\b",
        rf"\b{short_month}\s+{day}(?:st|nd|rd|th)?,?\s+{year}\b",
    )

    if any(re.search(pattern, normalized) for pattern in full_date_patterns):
        return True

    # A date without a year is accepted only in an explicit confirmation
    # question, not a generic question such as "Can you pay on 7 November?"
    explicit_confirmation = (
        "confirm" in normalized
        or "a date you can confirm" in normalized
    )

    if not explicit_confirmation or "?" not in message:
        return False

    yearless_patterns = (
        rf"\b{day}(?:st|nd|rd|th)?\s+{month}\b",
        rf"\b{day}(?:st|nd|rd|th)?\s+{short_month}\b",
        rf"\b{month}\s+{day}(?:st|nd|rd|th)?\b",
        rf"\b{short_month}\s+{day}(?:st|nd|rd|th)?\b",
    )

    return any(re.search(pattern, normalized) for pattern in yearless_patterns)




def _contains_conflicting_date(
    message: str,
    expected_date: date,
) -> bool:
    """Reject a response that explicitly mentions another full date."""
    patterns = (
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\b",
        r"\b[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    )

    for pattern in patterns:
        for match in re.finditer(pattern, message, re.IGNORECASE):
            token = match.group(0).strip(" ,.")
            clean_token = re.sub(
                r"(\d{1,2})(?:st|nd|rd|th)", r"\1", token, flags=re.IGNORECASE
            )
            clean_token = re.sub(r"\s+", " ", clean_token)
            parsed_date = None

            for fmt in (
                "%Y-%m-%d",
                "%d %B %Y",
                "%d %b %Y",
                "%B %d %Y",
                "%B %d, %Y",
                "%b %d %Y",
                "%b %d, %Y",
            ):
                try:
                    parsed_date = (
                        date.fromisoformat(clean_token)
                        if fmt == "%Y-%m-%d"
                        else datetime.strptime(clean_token, fmt).date()
                    )
                    break
                except ValueError:
                    continue

            if parsed_date is not None and parsed_date != expected_date:
                return True

    return False



def _is_date_confirmation_question(message: str, payment_date: date) -> bool:
    """Validate that the agent asked a date-specific payment confirmation question."""
    if not message or "?" not in message:
        return False

    raw_sentences = re.split(r"(?<=[.!?\n;])\s+", message.strip())
    confirmation_phrases = (
        "can you pay",
        "will you pay",
        "could you pay",
        "would you be able to pay",
        "will you be able to pay",
        "can you make the payment",
        "could you make the payment",
        "would you be able to make the payment",
        "will you be able to make the payment",
        "can you confirm",
        "could you confirm",
        "would you confirm",
        "will you confirm",
        "confirm that you can pay",
        "confirm that you will pay",
        "a date you can confirm",
        "confirm",
    )

    for sent in reversed(raw_sentences):
        sent = sent.strip()
        if not sent.endswith("?"):
            continue

        sent_lower = sent.lower()
        matched_phrase = next(
            (p for p in confirmation_phrases if p in sent_lower),
            None,
        )
        if not matched_phrase:
            continue

        if matched_phrase == "confirm" and not any(
            k in sent_lower for k in ("pay", "payment", "date")
        ):
            continue

        if _date_is_in_message(sent, payment_date):
            return True

    return False


def verify_payment_confirmation(
    *,
    customer_response: str,
    proposed_payment_date: date | None,
    agent_last_message: str,
    pending_proposal_date: date | None,
) -> ConfirmationResult:
    """Validate explicit customer consent for the pending payment date."""

    if not isinstance(customer_response, str) or not customer_response.strip():
        return ConfirmationResult(
            ConfirmationStatus.AMBIGUOUS,
            None,
            "Customer response is empty.",
        )

    if not agent_last_message or pending_proposal_date is None:
        return ConfirmationResult(
            ConfirmationStatus.NO_PENDING_PROPOSAL,
            None,
            "No pending date-specific proposal is available.",
        )

    if (
        proposed_payment_date is not None
        and proposed_payment_date != pending_proposal_date
    ):
        return ConfirmationResult(
            ConfirmationStatus.NO_PENDING_PROPOSAL,
            None,
            "The extracted date differs from the pending proposal.",
        )

    if not _is_date_confirmation_question(
        agent_last_message, pending_proposal_date
    ):
        return ConfirmationResult(
            ConfirmationStatus.NO_PENDING_PROPOSAL,
            None,
            "The last agent message is not a date-specific confirmation question.",
        )

    normalized = _normalize(customer_response)

    # Reject a response that explicitly names a different full date.
    if _contains_conflicting_date(customer_response, pending_proposal_date):
        return ConfirmationResult(
            ConfirmationStatus.AMBIGUOUS,
            None,
            "The response mentions a different payment date.",
        )

    # Uncertainty takes priority over negative wording.
    if any(
        re.search(pattern, normalized)
        for pattern in _AMBIGUOUS_PATTERNS
    ):
        return ConfirmationResult(
            ConfirmationStatus.AMBIGUOUS,
            None,
            "The customer's response is tentative or uncertain.",
        )

    if any(
        re.search(pattern, normalized)
        for pattern in _NEGATIVE_PATTERNS
    ):
        return ConfirmationResult(
            ConfirmationStatus.NOT_CONFIRMED,
            None,
            "The customer did not accept the proposed date.",
        )

    explicit_affirmative = any(
        normalized == phrase
        or (
            normalized.startswith(phrase + " ")
            and not any(
                re.search(pattern, normalized)
                for pattern in _AMBIGUOUS_PATTERNS
            )
        )
        for phrase in _EXPLICIT_YES_PATTERNS
    )

    if explicit_affirmative:
        return ConfirmationResult(
            ConfirmationStatus.CONFIRMED,
            pending_proposal_date,
            "The customer explicitly accepted the pending date.",
        )

    return ConfirmationResult(
        ConfirmationStatus.AMBIGUOUS,
        None,
        "The response does not match a supported confirmation pattern.",
    )
