from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True
    )
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )
    preferred_language: Mapped[str] = mapped_column(
        String(30), default="English"
    )