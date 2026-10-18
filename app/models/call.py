from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Call(Base):
    __tablename__ = "calls"

    call_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    emi_id: Mapped[int] = mapped_column(
        ForeignKey("emis.emi_id"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'INITIATED'")
    )
    reason: Mapped[str | None] = mapped_column(String(255))
    expected_salary_date: Mapped[date | None] = mapped_column(Date)
    pending_payment_date: Mapped[date | None] = mapped_column(Date)
    promised_payment_date: Mapped[date | None] = mapped_column(Date)
    payment_commitment_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    call_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
