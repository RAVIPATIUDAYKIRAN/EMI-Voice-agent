
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.customers import router as customers_router
from app.api.calls import router as calls_router
from app.api.conversations import router as conversations_router
from app.db.database import check_database_connection, close_database

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_database()


app = FastAPI(
    title="AI EMI Collection Voice Agent",
    description="Customer EMI lookup and collection call APIs",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(customers_router)
app.include_router(calls_router)
app.include_router(conversations_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/database")
async def database_health():
    try:
        result = await check_database_connection()
        return {
            "status": "ok",
            "database": result["database"],
        }
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable",
        ) from None
