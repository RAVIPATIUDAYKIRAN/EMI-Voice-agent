
import asyncio
import logging

import httpx
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config.settings import settings

logger = logging.getLogger(__name__)

MODEL = settings.openai_model

api_key = settings.openai_api_key.get_secret_value().strip()
if not api_key:
    logger.warning("OPENAI_API_KEY is not set or empty in environment.")

client = AsyncOpenAI(
    api_key=api_key or "missing_key",
    timeout=httpx.Timeout(30.0, connect=5.0),
    max_retries=0,
)


def is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True

    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 409, 429, 500, 502, 503, 504}

    return False


@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, min=1, max=5),
    reraise=True,
)
async def generate_agent_reply(
    customer_name: str,
    emi_amount: str,
    due_date: str,
    conversation_history: list[dict[str, str]],
    commitment_confirmed: bool = False,
    suggested_payment_date: str | None = None,
) -> str:
    instructions = (
        "You are a polite, professional EMI collection voice assistant for a financial institution. "
        "Be respectful, concise, empathetic, and strictly non-coercive. Never threaten, "
        "shame, harass, or pressure the customer. Use only the supplied "
        "customer and EMI facts. Never claim that a payment was made or received. "
        "Do not invent dates, amounts, policies, or account details. "
        "Always ensure proper spacing, spelling, and grammar without accidental word concatenations. "
        "Ask at most one clear follow-up question when needed. "
        "Payment date confirmation rules: "
        "- If the customer mentions, proposes, or suggests a payment date (whether tentative or definite, "
        "such as 'I may be able to pay on 28 October 2026', or even if their salary is delayed): "
        "you MUST acknowledge their situation politely and ask a clear, date-specific confirmation question "
        f"for that exact date, such as: 'Would you be able to pay the INR {emi_amount} on [date]?' or "
        "'Can you confirm that you will pay on [date]?'. "
        "Always prioritize asking for confirmation of that payment date over asking when salary will arrive. "
        "Specific customer scenarios: "
        "- If the customer says salary is delayed WITHOUT mentioning any payment date: acknowledge politely and ask when they expect their salary. "
        "- If the customer says they will pay after salary WITHOUT mentioning any payment date: ask for their expected salary date or concrete payment timing. "
        "- If the customer is unavailable, busy, or asks for a callback: acknowledge politely, confirm that they will be called back later without assuming or inventing a callback time when none was given, and conclude the interaction courteously. Do not ask for or create a payment commitment. "
        "- If the customer says insufficient funds or unable to pay: respond with empathy and respect. Never pressure, shame, or coerce the customer. You may gently and optionally ask if they have an approximate timing in mind, but keep it strictly optional. If the customer indicates they cannot provide a date, declines to provide one, cannot commit, or indicates the conversation should end: acknowledge politely, do not ask for a date again, note that their situation is recorded, and close the interaction courteously. "
        "- If the customer cannot pay or refuses: respond respectfully without pressure, and do not invent promises. "
        "- If the customer says they already paid: politely acknowledge and mention that the transaction can be verified. "
        "- If the customer says wrong number or wrong person: apologize politely for the disturbance and conclude. "
        "A payment commitment is confirmed ONLY by the backend. "
        f"Backend-verified payment commitment: "
        f"{'CONFIRMED' if commitment_confirmed else 'NOT CONFIRMED'}. "
        "If the backend says CONFIRMED, acknowledge the agreed "
        "payment date if it is available in the conversation and "
        "close politely. Do not ask for another payment date. "
        "If the backend says NOT CONFIRMED, do not claim a commitment "
        "was saved. Continue respectfully and ask a relevant "
        "follow-up question when appropriate. "
        f"Customer name: {customer_name}. "
        f"EMI amount: INR {emi_amount}. Due date: {due_date}."
    )
    if suggested_payment_date and not commitment_confirmed:
        instructions += (
            f" Note: The customer mentioned a potential payment date of {suggested_payment_date}. "
            f"You MUST ask a date-specific confirmation question for {suggested_payment_date}, "
            f"for example: 'Would you be able to pay the INR {emi_amount} on {suggested_payment_date}?'."
        )

    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=MODEL,
                instructions=instructions,
                input=conversation_history,
            ),
            timeout=35.0,
        )
        reply = response.output_text.strip()

        if not reply:
            raise RuntimeError("The AI model returned an empty response")

        return reply

    except asyncio.TimeoutError:
        logger.warning("AI response exceeded the application timeout")
        raise
    except Exception:
        logger.exception("AI response generation failed")
        raise
