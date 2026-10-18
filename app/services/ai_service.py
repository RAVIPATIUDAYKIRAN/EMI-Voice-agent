
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

api_key = settings.openai_api_key.get_secret_value().strip()
MODEL = settings.openai_model

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
    preferred_language: str | None = None,
    opening_turn: bool = False,
    emi_status: str | None = None,
) -> str:
    if not settings.has_openai_credentials:
        raise RuntimeError("OpenAI is not configured")
    instructions = (
        "You are a polite, professional EMI collection voice assistant for a financial institution. "
        "Be respectful, concise, empathetic, and strictly non-coercive. Never threaten, "
        "shame, harass, or pressure the customer. Use only the supplied "
        "customer and EMI facts. Never claim that a payment was made or received. "
        "Do not invent dates, amounts, policies, or account details. "
        "Always ensure proper spacing, spelling, and grammar without accidental word concatenations. "
        f"Reply in the customer's preferred language ({preferred_language or 'the language of the latest customer message'}). "
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
    if opening_turn:
        status_text = emi_status or "due"
        instructions += (
            " This is the opening turn of a simulated EMI reminder conversation. "
            "Greet the customer by name, identify yourself as an automated EMI assistant "
            "without inventing a bank name, state the supplied EMI amount and status, "
            "and politely ask the reason for the delay. Do not claim a payment was made "
            "or that a commitment is confirmed. "
            f"The recorded EMI status is {status_text}."
        )

    # The terminal CLI intentionally starts with no prior messages. The
    # Responses API still requires input, so give the model an explicit task;
    # customer and EMI facts remain in the instructions above.
    response_input: str | list[dict[str, str]] = conversation_history
    if opening_turn and not conversation_history:
        response_input = (
            "Generate the opening message now using the customer and EMI context "
            "in the instructions."
        )

    if isinstance(response_input, str):
        has_input = bool(response_input.strip())
    elif isinstance(response_input, list):
        has_input = any(
            isinstance(message, dict)
            and isinstance(message.get("content"), str)
            and message["content"].strip()
            for message in response_input
        )
    else:
        has_input = False
    if not has_input:
        raise RuntimeError("Cannot generate an AI reply without conversation input")

    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=MODEL,
                instructions=instructions,
                input=response_input,
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
    except Exception as exc:
        logger.error("AI response generation failed (%s)", type(exc).__name__)
        raise
