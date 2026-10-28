from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import LanguageCode
from app.db.base import Base


class MeetingStatus(StrEnum):
    CREATED = "created"
    MEDIA_UPLOADED = "media_uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=LanguageCode.MIXED.value,
    )
    output_language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=LanguageCode.MIXED.value,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MeetingStatus.CREATED.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )