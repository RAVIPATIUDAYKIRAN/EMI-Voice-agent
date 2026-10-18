"""Run one simulated EMI conversation using the existing FastAPI handlers."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Callable

# Support the documented invocation from the project root on Windows.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.calls import start_simulated_call
from app.api.conversations import (
    CustomerMessageRequest,
    generate_call_summary,
    send_customer_message,
)
from app.config.settings import settings
from app.db.database import AsyncSessionLocal, close_database
from app.models import ConversationMessage, Customer
from app.services.ai_service import generate_agent_reply


async def _create_simulated_call(customer_id: int):
    """Use the production call route and retrieve the stored language preference."""
    async with AsyncSessionLocal() as db:
        call = await start_simulated_call(
            customer_id=customer_id,
            channel="simulated",
            db=db,
        )
        result = await db.execute(
            select(Customer).where(Customer.customer_id == customer_id)
        )
        customer = result.scalar_one_or_none()
        if customer is None:
            raise HTTPException(status_code=409, detail="Call customer is unavailable")
        preferred_language = customer.preferred_language
        # The call route committed its transaction; close this read transaction
        # before making the remote model request.
        await db.rollback()
        return call, preferred_language


async def _store_agent_message(call_id: int, message: str) -> None:
    async with AsyncSessionLocal() as db:
        db.add(
            ConversationMessage(
                call_id=call_id,
                speaker="AGENT",
                message_text=message,
            )
        )
        await db.commit()


async def _submit_customer_message(call_id: int, message: str):
    async with AsyncSessionLocal() as db:
        return await send_customer_message(
            call_id,
            CustomerMessageRequest(message=message),
            db,
        )


async def _load_summary(call_id: int):
    async with AsyncSessionLocal() as db:
        return await generate_call_summary(call_id, db)


def _read_customer_id(input_fn: Callable[[str], str], output_fn: Callable[[str], None]) -> int | None:
    while True:
        try:
            value = input_fn("Customer ID (or 'exit'): ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("Session ended. No call was started.")
            return None
        if value.lower() == "exit":
            output_fn("Session ended. No call was started.")
            return None
        if value.isdecimal() and int(value) > 0:
            return int(value)
        output_fn("Enter a positive customer ID, or type 'exit'.")


async def run_terminal_conversation(
    customer_id: int | None = None,
    *,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> int:
    """Run a text-only simulated conversation; never invokes Twilio."""
    read = input if input_fn is None else input_fn
    write = print if output_fn is None else output_fn

    if customer_id is None:
        customer_id = _read_customer_id(read, write)
        if customer_id is None:
            return 0
    elif customer_id <= 0:
        write("Customer ID must be a positive integer.")
        return 2

    if not settings.has_openai_credentials:
        write("OpenAI is not configured. Set OPENAI_API_KEY and restart the application.")
        return 2

    try:
        call, preferred_language = await _create_simulated_call(customer_id)
    except HTTPException as exc:
        write(f"Could not start the simulated conversation: {exc.detail}")
        return 1
    except SQLAlchemyError:
        write("The database is unavailable. No conversation was started.")
        return 1

    try:
        opening = await generate_agent_reply(
            customer_name=call.customer_name,
            emi_amount=f"{call.emi_amount:.2f}",
            due_date=call.due_date,
            conversation_history=[],
            commitment_confirmed=False,
            preferred_language=preferred_language,
            opening_turn=True,
            emi_status=call.emi_status,
        )
        await _store_agent_message(call.call_id, opening)
    except Exception:
        write(
            "Could not generate the opening message. "
            f"Simulated call {call.call_id} was created but no conversation turn was saved."
        )
        return 1

    write(f"AI Agent: {opening}")
    write("Type 'exit' at any customer prompt to end the simulation.")

    while True:
        try:
            customer_message = read("Customer: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("Session ended. No further customer message was sent.")
            return 0

        if customer_message.lower() == "exit":
            write("Session ended. No commitment was added unless the agent confirmed it.")
            return 0
        if not customer_message:
            write("Please enter a response, or type 'exit'.")
            continue
        if len(customer_message) > 2000:
            write("That response is too long. Please keep it under 2,000 characters.")
            continue

        try:
            response = await _submit_customer_message(call.call_id, customer_message)
        except HTTPException as exc:
            write(f"Message was not processed: {exc.detail}")
            continue
        except (SQLAlchemyError, ValueError):
            write("Message could not be processed. Please retry or type 'exit'.")
            continue
        except Exception:
            # Avoid printing provider exception text, which may contain request data.
            write("The conversation service is unavailable. Please retry or type 'exit'.")
            continue

        write(f"AI Agent: {response.agent_reply}")
        if response.commitment_status == "CONFIRMED":
            write("Commitment status: CONFIRMED")
        elif response.commitment_status == "NEEDS_CLARIFICATION":
            write("Commitment status: needs clarification; no commitment was saved.")
        elif response.commitment_status == "DECLINED":
            write("Commitment status: declined; no commitment was saved.")

        if response.call_status == "COMPLETED":
            try:
                summary = await _load_summary(call.call_id)
                write(f"Call summary: {summary.call_summary}")
                write(
                    "Payment commitment: "
                    + ("CONFIRMED" if summary.payment_commitment_confirmed else "NOT_CONFIRMED")
                )
            except Exception:
                write("The conversation is complete; the summary could not be loaded.")
            return 0


async def _async_main(customer_id: int | None) -> int:
    try:
        return await run_terminal_conversation(customer_id)
    finally:
        await close_database()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a simulated, text-only EMI conversation in the terminal."
    )
    parser.add_argument(
        "--customer-id",
        type=int,
        help="Existing customer ID; if omitted, the terminal prompts for one.",
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(args.customer_id))


if __name__ == "__main__":
    raise SystemExit(main())
