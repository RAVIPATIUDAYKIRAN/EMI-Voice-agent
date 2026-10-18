"""Small, deterministic language cues for respectful conversation handling.

These labels are operational hints, not a psychological assessment. The
classifier deliberately uses a small bilingual phrase list and does not send
customer text to another provider.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SentimentAssessment:
    sentiment: str
    stress_level: str
    score: int | None


_DISTRESS = (
    "worried", "stress", "stressed", "anxious", "afraid", "cannot pay",
    "can't pay", "no money", "job loss", "hospital", "emergency",
    "परेशान", "चिंता", "तनाव", "डर", "पैसे नहीं", "नहीं कर सकता",
    "नहीं कर सकती", "नौकरी चली", "अस्पताल", "आपातकाल",
)
_POSITIVE = (
    "thank you", "thanks", "yes, i confirm", "i will pay", "मैं भुगतान करूंगा",
    "मैं भुगतान करूँगा", "मैं भुगतान करूंगी", "मैं भुगतान करूँगी", "धन्यवाद",
)
_HIGH_STRESS = (
    "panic", "desperate", "threat", "suicid", "harm myself",
    "आत्महत्या", "खुद को नुकसान", "बहुत ज्यादा तनाव", "घबराहट",
)


def assess_sentiment(text: str) -> SentimentAssessment:
    normalized = " ".join((text or "").casefold().split())
    distress_hits = sum(phrase in normalized for phrase in _DISTRESS)
    positive_hits = sum(phrase in normalized for phrase in _POSITIVE)
    high_stress = any(phrase in normalized for phrase in _HIGH_STRESS)

    score = max(-3, min(3, positive_hits - distress_hits))
    if distress_hits or high_stress:
        # Report lexical cues, not an assertion about the customer's actual
        # psychological state.
        sentiment = "DISTRESS_CUE"
    elif positive_hits:
        sentiment = "POSITIVE_CUE"
    else:
        sentiment = "UNCERTAIN"

    stress_level = "HIGH" if high_stress or distress_hits >= 2 else (
        "ELEVATED" if distress_hits else "UNKNOWN"
    )
    return SentimentAssessment(
        sentiment,
        stress_level,
        score if distress_hits or positive_hits or high_stress else None,
    )
