from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EMI(Base):
    __tablename__ = "emis"

    emi_id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    emi_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )