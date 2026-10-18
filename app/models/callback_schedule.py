from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CallbackSchedule(Base):
    __tablename__ = "callback_schedules"

    callback_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.call_id", ondelete="CASCADE"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
