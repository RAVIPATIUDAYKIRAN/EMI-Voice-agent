
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    message_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.call_id", ondelete="CASCADE"),
        nullable=False,
    )

    speaker: Mapped[str] = mapped_column(
        String(10), nullable=False
    )

    message_text: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
