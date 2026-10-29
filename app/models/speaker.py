from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    meeting_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    speaker_label: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True)
    segment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    total_duration_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0)
    average_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    overlap_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    is_user_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
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
