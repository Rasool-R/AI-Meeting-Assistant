from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.media_asset import MediaAsset
from app.models.meeting import Meeting, MeetingStatus
from app.schemas.media import MediaAssetResponse, MeetingStatusResponse
from app.schemas.meeting import MeetingCreate, MeetingListResponse, MeetingResponse
from app.services.media_storage import store_validated_media_file

router = APIRouter(prefix="/meetings", tags=["meetings"])

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
UploadedMediaFile = Annotated[UploadFile, File(...)]


def get_meeting_or_404(meeting_id: str, db: Session) -> Meeting:
    stmt = select(Meeting).where(Meeting.id == meeting_id)
    meeting = db.scalar(stmt)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    return meeting


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
    return get_meeting_or_404(meeting_id, db)


@router.post(
    "/{meeting_id}/media",
    response_model=MediaAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_meeting_media(
    meeting_id: str,
    file: UploadedMediaFile,
    db: DbSession,
    settings: AppSettings,
) -> MediaAsset:
    meeting = get_meeting_or_404(meeting_id, db)
    stored = store_validated_media_file(
        meeting_id=meeting.id,
        upload_file=file,
        settings=settings,
    )

    media_asset = MediaAsset(
        meeting_id=meeting.id,
        original_filename=stored.original_filename,
        stored_filename=stored.stored_filename,
        storage_path=stored.storage_path,
        content_type=stored.content_type,
        file_extension=stored.file_extension,
        file_size_bytes=stored.file_size_bytes,
        sha256_checksum=stored.sha256_checksum,
    )
    meeting.status = MeetingStatus.MEDIA_UPLOADED.value
    db.add(media_asset)
    db.commit()
    db.refresh(media_asset)
    return media_asset


@router.get("/{meeting_id}/status", response_model=MeetingStatusResponse)
def get_meeting_status(
    meeting_id: str,
    db: DbSession,
) -> MeetingStatusResponse:
    meeting = get_meeting_or_404(meeting_id, db)
    assets_stmt = (
        select(MediaAsset)
        .where(MediaAsset.meeting_id == meeting.id)
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    )
    assets = list(db.scalars(assets_stmt).all())
    latest_asset = (
        MediaAssetResponse.model_validate(assets[0]) if assets else None
    )

    return MeetingStatusResponse(
        meeting_id=meeting.id,
        status=MeetingStatus(meeting.status),
        primary_language=meeting.primary_language,
        output_language=meeting.output_language,
        media_assets_count=len(assets),
        latest_media_asset=latest_asset,
        updated_at=meeting.updated_at,
    )
