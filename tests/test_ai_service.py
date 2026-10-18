
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from app.services.ai_service import generate_agent_reply


async def main():
    history = [
        {
            "role": "user",
            "content": (
                "The customer says: My salary is delayed. "
                "I expect it on 7 November 2026."
            ),
        }
    ]

    try:
        reply = await generate_agent_reply(
            customer_name="Rahul Sharma",
            emi_amount="8500.00",
            due_date="2026-10-10",
            conversation_history=history,
        )

        print("\nAI SERVICE TEST: SUCCESS")
        print("Agent reply:", reply)

    except Exception as exc:
        logging.error(
            "AI service test failed with error type: %s",
            type(exc).__name__,
        )
        print("AI SERVICE TEST: FAILED")
        print("Error type:", type(exc).__name__)


if __name__ == "__main__":
    asyncio.run(main())
