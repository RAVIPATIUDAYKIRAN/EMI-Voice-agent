
from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.customers import router as customers_router
from app.api.calls import router as calls_router
from app.api.conversations import router as conversations_router
from app.api.dashboard import router as dashboard_router
from app.api.telephony import router as telephony_router
from app.api.callbacks import router as callbacks_router
from app.api.local_voice import router as local_voice_router
from app.config.settings import settings
from app.services.callback_service import callback_worker
from app.db.database import check_database_connection, close_database

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    worker_task = None
    if settings.callback_worker_enabled:
        worker_task = asyncio.create_task(callback_worker(stop_event))
    try:
        yield
    finally:
        if worker_task is not None:
            stop_event.set()
            await worker_task
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
app.include_router(dashboard_router)
app.include_router(telephony_router)
app.include_router(callbacks_router)
app.include_router(local_voice_router)


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
