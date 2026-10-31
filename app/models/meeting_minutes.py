from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import LanguageCode
from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class MeetingMinutes(Base):
    __tablename__ = "meeting_minutes"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    meeting_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    output_language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=LanguageCode.MIXED.value,
    )
    overall_summary: Mapped[str] = mapped_column(Text, nullable=False)
    topic_summaries_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    key_discussion_points_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    decisions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    action_items_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    unresolved_questions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    contradictions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    follow_up_items_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    repair_attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_user_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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