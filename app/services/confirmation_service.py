
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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


HINDI_MONTH_MAP: dict[str, int] = {
    "जनवरी": 1,
    "फ़रवरी": 2,
    "फरवरी": 2,
    "मार्च": 3,
    "अप्रैल": 4,
    "मई": 5,
    "जून": 6,
    "जुलाई": 7,
    "अगस्त": 8,
    "सितंबर": 9,
    "सितम्बर": 9,
    "अक्टूबर": 10,
    "अक्तूबर": 10,
    "नवंबर": 11,
    "नवम्बर": 11,
    "दिसंबर": 12,
    "दिसम्बर": 12,
}

HINDI_MONTH_NAMES_BY_NUM: dict[int, tuple[str, ...]] = {
    1: ("जनवरी",),
    2: ("फ़रवरी", "फरवरी"),
    3: ("मार्च",),
    4: ("अप्रैल",),
    5: ("मई",),
    6: ("जून",),
    7: ("जुलाई",),
    8: ("अगस्त",),
    9: ("सितंबर", "सितम्बर"),
    10: ("अक्टूबर", "अक्तूबर"),
    11: ("नवंबर", "नवम्बर"),
    12: ("दिसंबर", "दिसम्बर"),
}

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
HINDI_MONTHS_REGEX = "|".join(HINDI_MONTH_MAP.keys())

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
    # Multilingual / Hindi ambiguous patterns
    r"(?<!\S)शायद(?!\S)",
    r"(?<!\S)संभवतः(?!\S)",
    r"कोशिश करूँगा",
    r"कोशिश करूंगा",
    r"कोशिश करूँगी",
    r"कोशिश करूंगी",
    r"(?<!\S)कोशिश(?!\S)",
    r"पक्का नहीं",
    r"निश्चित नहीं",
    r"पता नहीं",
    r"सोचता हूँ",
    r"सोचता हूं",
    r"सोचती हूँ",
    r"सोचती हूं",
    r"देखता हूँ",
    r"देखता हूं",
    r"देखती हूँ",
    r"देखती हूं",
    r"गारंटी नहीं",
    r"गारन्टी नहीं",
    r"सैलरी के बाद",
    r"वेतन के बाद",
    r"तनख्वाह के बाद",
    r"सैलरी आएगी तब",
    r"जब सैलरी आएगी",
    r"उम्मीद है",
    r"आशा है",
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
    # Multilingual / Hindi negative patterns
    r"(?<!\S)नहीं(?!\S)",
    r"(?<!\S)ना(?!\S)",
    r"भुगतान नहीं",
    r"नहीं करूँगा",
    r"नहीं करूंगा",
    r"नहीं करूँगी",
    r"नहीं करूंगी",
    r"नहीं कर सकता",
    r"नहीं कर सकती",
    r"नहीं कर पाऊँगा",
    r"नहीं कर पाऊंगा",
    r"पुष्टि नहीं",
    r"(?<!\S)मना(?!\S)",
    r"उस तारीख को नहीं",
    r"उस तारीख पर नहीं",
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
    # Multilingual / Hindi affirmative patterns
    r"हाँ",
    r"हाँ मैं",
    r"हाँ मैं करूँगा",
    r"हाँ मैं करूंगा",
    r"हाँ मैं करूँगी",
    r"हाँ मैं करूंगी",
    r"हाँ मैं कर सकता हूँ",
    r"हाँ मैं कर सकता हूं",
    r"हाँ मैं कर सकती हूँ",
    r"हाँ मैं कर सकती हूं",
    r"हाँ मैं पुष्टि करता हूँ",
    r"हाँ मैं पुष्टि करता हूं",
    r"हाँ मैं पुष्टि करती हूँ",
    r"हाँ मैं पुष्टि करती हूं",
    r"हाँ मैं भुगतान करूँगा",
    r"हाँ मैं भुगतान करूंगा",
    r"हाँ मैं भुगतान करने की पुष्टि करता हूँ",
    r"हाँ मैं भुगतान करने की पुष्टि करता हूं",
    r"हाँ बिल्कुल",
    r"हाँ बिलकुल",
    r"हाँ ज़रूर",
    r"हाँ जरूर",
    r"जी हाँ",
    r"जी हां",
    r"जी हाँ मैं",
    r"जी हां मैं",
    r"जी हाँ बिल्कुल",
    r"जी हाँ जरूर",
    r"जी हाँ ज़रूर",
    r"जी हाँ मैं पुष्टि करता हूँ",
    r"जी हाँ मैं पुष्टि करता हूं",
    r"जी हाँ मैं भुगतान करूँगा",
    r"जी हाँ मैं भुगतान करूंगा",
    r"पुष्टि करता हूँ",
    r"पुष्टि करता हूं",
    r"पुष्टि करती हूँ",
    r"पुष्टि करती हूं",
    r"मैं पुष्टि करता हूँ",
    r"मैं पुष्टि करता हूं",
    r"मैं पुष्टि करती हूँ",
    r"मैं पुष्टि करती हूं",
    r"मैं भुगतान करने की पुष्टि करता हूँ",
    r"मैं भुगतान करने की पुष्टि करता हूं",
    r"मैं भुगतान करूँगा",
    r"मैं भुगतान करूंगा",
    r"मैं भुगतान करूँगी",
    r"मैं भुगतान करूंगी",
    r"मैं कर दूँगा",
    r"मैं कर दूंगा",
    r"स्वीकार है",
    r"बिल्कुल",
    r"बिलकुल",
    r"ज़रूर",
    r"जरूर",
    r"ठीक है मैं भुगतान करूँगा",
    r"ठीक है मैं भुगतान करूंगा",
)

CONFIRMATION_QUESTION_PHRASES = (
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
    # Hindi phrases
    "भुगतान कर सकते हैं",
    "भुगतान कर सकती हैं",
    "भुगतान करेंगे",
    "भुगतान करेंगी",
    "भुगतान कर देंगे",
    "भुगतान कर देंगी",
    "भुगतान करने का वादा कर सकते हैं",
    "भुगतान करने का वादा कर सकती हैं",
    "भुगतान करने का वादा करेंगे",
    "भुगतान करने का वादा करेंगी",
    "भुगतान कर पाएंगे",
    "भुगतान कर पाएंगी",
    "भुगतान कर पाएँगे",
    "भुगतान कर पाएँगी",
    "भुगतान कर पायेंगे",
    "भुगतान कर पायेंगी",
    "भुगतान कर सकते",
    "भुगतान करेंगे",
    "पुष्टि कर सकते हैं",
    "पुष्टि कर सकती हैं",
    "पुष्टि करेंगे",
    "पुष्टि करेंगी",
    "पुष्टि करते हैं",
    "पुष्टि करती हैं",
    "पुष्टि करें",
    "पुष्टि",
)


def _normalize(value: str) -> str:
    """Normalize text preserving Unicode letters, marks (accents/matras), and numbers."""
    value = value.lower().replace("’", "'")
    chars: list[str] = []
    for ch in value:
        cat = unicodedata.category(ch)
        if cat.startswith(("L", "M", "N")) or ch in "' ":
            chars.append(ch)
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def extract_dates_from_text(text: str, default_year: int | None = None) -> list[tuple[int, date, bool]]:
    """
    Extract candidate payment dates from text across ISO, English, and Hindi formats.
    Returns list of (start_position, parsed_date, has_explicit_year).
    """
    text_clean = text.translate(DEVANAGARI_DIGITS)
    results: list[tuple[int, date, bool]] = []
    seen_positions: set[int] = set()

    # 1. ISO date: YYYY-MM-DD
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text_clean):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            results.append((m.start(), d, True))
            seen_positions.add(m.start())
        except ValueError:
            pass

    # 2. English date with year
    patterns_eng_year = (
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b", "%d %B %Y"),
        (r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", "%B %d %Y"),
    )
    for pat, fmt in patterns_eng_year:
        for m in re.finditer(pat, text_clean, re.IGNORECASE):
            token = re.sub(r"(\d{1,2})(?:st|nd|rd|th)", r"\1", m.group(0), flags=re.IGNORECASE)
            token = re.sub(r"\s+", " ", token.replace(",", ""))
            for try_fmt in (fmt, fmt.replace("%B", "%b")):
                try:
                    d = datetime.strptime(token, try_fmt).date()
                    results.append((m.start(), d, True))
                    seen_positions.add(m.start())
                    break
                except ValueError:
                    continue

    # 3. Hindi date with year
    hindi_pattern_with_year = rf"\b(\d{{1,2}})\s+({HINDI_MONTHS_REGEX})\s+(\d{{4}})\b"
    for m in re.finditer(hindi_pattern_with_year, text_clean):
        day = int(m.group(1))
        month = HINDI_MONTH_MAP[m.group(2)]
        year = int(m.group(3))
        try:
            results.append((m.start(), date(year, month, day), True))
            seen_positions.add(m.start())
        except ValueError:
            pass

    hindi_pattern_m_first_year = rf"\b({HINDI_MONTHS_REGEX})\s+(\d{{1,2}}),?\s+(\d{{4}})\b"
    for m in re.finditer(hindi_pattern_m_first_year, text_clean):
        month = HINDI_MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3))
        try:
            results.append((m.start(), date(year, month, day), True))
            seen_positions.add(m.start())
        except ValueError:
            pass

    # Yearless dates
    year_val = default_year or date.today().year

    # English yearless
    patterns_eng_noyear = (
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\b", "%d %B"),
        (r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", "%B %d"),
    )
    for pat, fmt in patterns_eng_noyear:
        for m in re.finditer(pat, text_clean, re.IGNORECASE):
            if any(pos <= m.start() < pos + 30 for pos in seen_positions):
                continue
            token = re.sub(r"(\d{1,2})(?:st|nd|rd|th)", r"\1", m.group(0), flags=re.IGNORECASE)
            token = re.sub(r"\s+", " ", token)
            for try_fmt in (fmt, fmt.replace("%B", "%b")):
                try:
                    d = datetime.strptime(token, try_fmt).date().replace(year=year_val)
                    results.append((m.start(), d, False))
                    break
                except ValueError:
                    continue

    # Hindi yearless
    hindi_pattern_no_year = rf"\b(\d{{1,2}})\s+({HINDI_MONTHS_REGEX})\b"
    for m in re.finditer(hindi_pattern_no_year, text_clean):
        if any(pos <= m.start() < pos + 30 for pos in seen_positions):
            continue
        day = int(m.group(1))
        month = HINDI_MONTH_MAP[m.group(2)]
        try:
            results.append((m.start(), date(year_val, month, day), False))
        except ValueError:
            pass

    results.sort(key=lambda x: x[0])
    return results


def _date_is_in_message(message: str, payment_date: date) -> bool:
    """Require a full date, or a yearless date in explicit confirmation wording."""
    normalized = message.lower().translate(DEVANAGARI_DIGITS)

    # Full ISO date.
    if payment_date.isoformat() in normalized:
        return True

    day = payment_date.day
    year = payment_date.year
    month = payment_date.strftime("%B").lower()
    short_month = payment_date.strftime("%b").lower()

    full_date_patterns = [
        rf"\b{day}(?:st|nd|rd|th)?\s+{month}\s+{year}\b",
        rf"\b{day}(?:st|nd|rd|th)?\s+{short_month}\s+{year}\b",
        rf"\b{month}\s+{day}(?:st|nd|rd|th)?,?\s+{year}\b",
        rf"\b{short_month}\s+{day}(?:st|nd|rd|th)?,?\s+{year}\b",
    ]

    hindi_months = HINDI_MONTH_NAMES_BY_NUM.get(payment_date.month, ())
    for h_month in hindi_months:
        full_date_patterns.append(rf"(?<!\S){day}\s+{h_month}\s+{year}(?!\S)")
        full_date_patterns.append(rf"(?<!\S){h_month}\s+{day},?\s+{year}(?!\S)")

    if any(re.search(pattern, normalized) for pattern in full_date_patterns):
        return True

    # A date without a year is accepted only in an explicit confirmation
    # question, not a generic question such as "Can you pay on 7 November?"
    explicit_confirmation = (
        "confirm" in normalized
        or "a date you can confirm" in normalized
        or "पुष्टि" in normalized
    )

    if not explicit_confirmation or "?" not in message:
        return False

    yearless_patterns = [
        rf"\b{day}(?:st|nd|rd|th)?\s+{month}\b",
        rf"\b{day}(?:st|nd|rd|th)?\s+{short_month}\b",
        rf"\b{month}\s+{day}(?:st|nd|rd|th)?\b",
        rf"\b{short_month}\s+{day}(?:st|nd|rd|th)?\b",
    ]
    for h_month in hindi_months:
        yearless_patterns.append(rf"(?<!\S){day}\s+{h_month}(?!\S)")
        yearless_patterns.append(rf"(?<!\S){h_month}\s+{day}(?!\S)")

    return any(re.search(pattern, normalized) for pattern in yearless_patterns)


def _contains_conflicting_date(
    message: str,
    expected_date: date,
) -> bool:
    """Reject a response that explicitly mentions another full date."""
    found_dates = extract_dates_from_text(message)
    for _, parsed_date, has_year in found_dates:
        if has_year and parsed_date != expected_date:
            return True
    return False


def _amounts_in_text(message: str) -> list[Decimal]:
    """Read explicitly currency-qualified amounts without treating dates as amounts."""
    amounts: list[Decimal] = []
    for match in re.finditer(
        r"(?:₹|INR\b|Rs\.?\b)\s*([\d][\d,]*(?:\.\d{1,2})?)",
        message,
        re.IGNORECASE,
    ):
        try:
            amounts.append(Decimal(match.group(1).replace(",", "")).quantize(Decimal("0.01")))
        except InvalidOperation:
            continue
    return amounts


def _is_date_confirmation_question(message: str, payment_date: date) -> bool:
    """Validate that the agent asked a date-specific payment confirmation question."""
    if not message or "?" not in message:
        return False

    raw_sentences = re.split(r"(?<=[.!?\n;।])\s+", message.strip())

    for sent in reversed(raw_sentences):
        sent = sent.strip()
        if not sent.endswith("?"):
            continue

        sent_lower = sent.lower().translate(DEVANAGARI_DIGITS)
        matched_phrase = next(
            (p for p in CONFIRMATION_QUESTION_PHRASES if p in sent_lower),
            None,
        )
        if not matched_phrase:
            continue

        if matched_phrase in ("confirm", "पुष्टि") and not any(
            k in sent_lower for k in ("pay", "payment", "date", "भुगतान", "तारीख", "तारीख़")
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
    proposed_payment_amount: Decimal | float | int | None = None,
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

    expected_amount: Decimal | None = None
    if proposed_payment_amount is not None:
        try:
            expected_amount = Decimal(str(proposed_payment_amount)).quantize(
                Decimal("0.01")
            )
        except InvalidOperation:
            return ConfirmationResult(
                ConfirmationStatus.NO_PENDING_PROPOSAL,
                None,
                "The proposed payment amount is invalid.",
            )
        if expected_amount <= 0 or expected_amount not in _amounts_in_text(
            agent_last_message
        ):
            return ConfirmationResult(
                ConfirmationStatus.NO_PENDING_PROPOSAL,
                None,
                "The agent question does not include the EMI amount.",
            )

    normalized = _normalize(customer_response)

    # Reject a response that explicitly names a different full date.
    if _contains_conflicting_date(customer_response, pending_proposal_date):
        return ConfirmationResult(
            ConfirmationStatus.AMBIGUOUS,
            None,
            "The response mentions a different payment date.",
        )

    if expected_amount is not None and any(
        amount != expected_amount for amount in _amounts_in_text(customer_response)
    ):
        return ConfirmationResult(
            ConfirmationStatus.AMBIGUOUS,
            None,
            "The response mentions a different payment amount.",
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
        or (
            phrase in normalized
            and phrase in (
                "पुष्टि करता हूँ",
                "पुष्टि करता हूं",
                "पुष्टि करती हूँ",
                "पुष्टि करती हूं",
                "भुगतान करने की पुष्टि करता हूँ",
                "भुगतान करने की पुष्टि करता हूं",
                "स्वीकार है",
            )
            and not any(
                re.search(pattern, normalized)
                for pattern in _AMBIGUOUS_PATTERNS
            )
            and not any(
                re.search(pattern, normalized)
                for pattern in _NEGATIVE_PATTERNS
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
