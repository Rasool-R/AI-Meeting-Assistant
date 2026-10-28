from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.meeting import Meeting
from app.schemas.meeting import MeetingCreate, MeetingListResponse, MeetingResponse

router = APIRouter(prefix="/meetings", tags=["meetings"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: MeetingCreate,
    db: DbSession,
) -> Meeting:
    meeting = Meeting(
        title=payload.title,
        description=payload.description,
        primary_language=payload.primary_language.value,
        output_language=(
            payload.output_language or payload.primary_language).value,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


@router.get("", response_model=MeetingListResponse)
def list_meetings(
    db: DbSession,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MeetingListResponse:
    total_stmt = select(func.count()).select_from(Meeting)
    total = db.scalar(total_stmt) or 0

    items_stmt = (
        select(Meeting)
        .order_by(Meeting.created_at.desc(), Meeting.id.desc())
        .offset(skip)
        .limit(limit)
    )
    items = list(db.scalars(items_stmt).all())
    return MeetingListResponse(items=items, total=total)


@router.get("/{meeting_id}", response_model=MeetingResponse)
def get_meeting(
    meeting_id: str,
    db: DbSession,
) -> Meeting:
    stmt = select(Meeting).where(Meeting.id == meeting_id)
    meeting = db.scalar(stmt)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    return meeting
