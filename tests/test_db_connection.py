
import asyncio

from app.db.database import (
    check_database_connection,
    close_database,
)


async def main():
    try:
        result = await check_database_connection()
        print("Database connection successful")
        print(result)
    except Exception as exc:
        print("Database connection failed:", type(exc).__name__)
        raise
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
