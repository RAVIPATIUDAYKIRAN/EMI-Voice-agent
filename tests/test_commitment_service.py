
import asyncio
from dotenv import load_dotenv

load_dotenv()

from app.services.commitment_service import extract_commitment_details


async def main():
    history = [
        {
            "role": "user",
            "content": (
                "My salary is delayed. I expect it on 7 November 2026."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Would you be able to pay the INR 8,500 EMI "
                "on 7 November 2026?"
            ),
        },
    ]

    result = await extract_commitment_details(history)

    print("Extraction test successful")
    print(result.model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
