from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import LanguageCode, Settings, get_settings
from app.db.session import get_db
from app.models.media_asset import MediaAsset
from app.models.meeting import Meeting, MeetingStatus
from app.models.speaker import Speaker
from app.models.transcript_segment import TranscriptSegment
from app.schemas.diarization import (
    DiarizeMeetingRequest,
    MeetingDiarizationResponse,
    SpeakerResponse,
    SpeakerUpdateRequest,
)
from app.schemas.media import MediaAssetResponse, MeetingStatusResponse
from app.schemas.meeting import MeetingCreate, MeetingListResponse, MeetingResponse
from app.schemas.transcript import (
    MeetingTranscriptResponse,
    TranscribeMeetingRequest,
    TranscriptSegmentResponse,
)
from app.services.diarization import (
    DiarizationProvider,
    DiarizationProviderError,
    SegmentTimeWindow,
    align_turns_with_segments,
    get_diarization_provider,
)
from app.services.media_storage import store_validated_media_file
from app.services.transcription import (
    TranscriptionProvider,
    TranscriptionProviderError,
    get_transcription_provider,
)

router = APIRouter(prefix="/meetings", tags=["meetings"])

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
UploadedMediaFile = Annotated[UploadFile, File(...)]
SttProvider = Annotated[TranscriptionProvider,
                        Depends(get_transcription_provider)]
DiarizerProvider = Annotated[DiarizationProvider,
                             Depends(get_diarization_provider)]
TranscribeRequestPayload = Annotated[
    TranscribeMeetingRequest | None,
    Body(),
]
DiarizeRequestPayload = Annotated[
    DiarizeMeetingRequest | None,
    Body(),
]


def get_meeting_or_404(meeting_id: str, db: Session) -> Meeting:
    stmt = select(Meeting).where(Meeting.id == meeting_id)
    meeting = db.scalar(stmt)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    return meeting


def build_transcript_response(
    meeting: Meeting,
    segments: list[TranscriptSegment],
) -> MeetingTranscriptResponse:
    segment_models = [TranscriptSegmentResponse.model_validate(
        seg) for seg in segments]
    full_text = "\n".join(seg.text for seg in segments)
    return MeetingTranscriptResponse(
        meeting_id=meeting.id,
        primary_language=LanguageCode(meeting.primary_language),
        total_segments=len(segment_models),
        segments=segment_models,
        full_text=full_text,
    )


def rebuild_meeting_speakers(
    meeting_id: str,
    segments: list[TranscriptSegment],
    overlap_by_segment_id: dict[str, bool],
    db: Session,
) -> list[Speaker]:
    existing_stmt = select(Speaker).where(Speaker.meeting_id == meeting_id)
    existing_by_label = {
        spk.speaker_label: spk for spk in db.scalars(existing_stmt).all()
    }

    grouped: dict[str, list[TranscriptSegment]] = {}
    for seg in segments:
        label = seg.speaker_label or "SPEAKER_UNKNOWN"
        grouped.setdefault(label, []).append(seg)

    for label, existing_spk in list(existing_by_label.items()):
        if label not in grouped:
            db.delete(existing_spk)
            existing_by_label.pop(label)

    persisted_speakers: list[Speaker] = []
    for label in sorted(grouped.keys()):
        seg_list = grouped[label]
        total_duration = round(
            sum(max(0.0, s.end_time_seconds - s.start_time_seconds)
                for s in seg_list),
            2,
        )
        confidences = [
            s.confidence for s in seg_list if s.confidence is not None]
        avg_conf = (
            round(sum(confidences) / len(confidences),
                  4) if confidences else None
        )
        overlap_count = sum(
            1 for s in seg_list if overlap_by_segment_id.get(s.id, False)
        )

        speaker = existing_by_label.get(label)
        if speaker is None:
            speaker = Speaker(
                meeting_id=meeting_id,
                speaker_label=label,
                display_name=None,
                segment_count=len(seg_list),
                total_duration_seconds=total_duration,
                average_confidence=avg_conf,
                overlap_count=overlap_count,
                is_user_verified=False,
            )
            db.add(speaker)
        else:
            speaker.segment_count = len(seg_list)
            speaker.total_duration_seconds = total_duration
            speaker.average_confidence = avg_conf
            speaker.overlap_count = overlap_count

        persisted_speakers.append(speaker)

    db.commit()
    for spk in persisted_speakers:
        db.refresh(spk)
    return persisted_speakers


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
    latest_asset = MediaAssetResponse.model_validate(
        assets[0]) if assets else None

    return MeetingStatusResponse(
        meeting_id=meeting.id,
        status=MeetingStatus(meeting.status),
        primary_language=LanguageCode(meeting.primary_language),
        output_language=LanguageCode(meeting.output_language),
        media_assets_count=len(assets),
        latest_media_asset=latest_asset,
        updated_at=meeting.updated_at,
    )


@router.post(
    "/{meeting_id}/transcribe",
    response_model=MeetingTranscriptResponse,
    status_code=status.HTTP_200_OK,
)
def transcribe_meeting(
    meeting_id: str,
    db: DbSession,
    provider: SttProvider,
    payload: TranscribeRequestPayload = None,
) -> MeetingTranscriptResponse:
    meeting = get_meeting_or_404(meeting_id, db)
    options = payload or TranscribeMeetingRequest()

    existing_stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting.id)
        .order_by(TranscriptSegment.segment_index.asc())
    )
    existing_segments = list(db.scalars(existing_stmt).all())
    if existing_segments and not options.force_retranscribe:
        return build_transcript_response(meeting, existing_segments)

    latest_media_stmt = (
        select(MediaAsset)
        .where(MediaAsset.meeting_id == meeting.id)
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    )
    latest_media = db.scalar(latest_media_stmt)
    if latest_media is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot transcribe a meeting before uploading a media file.",
        )

    if existing_segments and options.force_retranscribe:
        db.execute(
            delete(TranscriptSegment).where(
                TranscriptSegment.meeting_id == meeting.id)
        )
        db.flush()

    meeting.status = MeetingStatus.PROCESSING.value
    db.commit()

    language_hint = options.language_override or LanguageCode(
        meeting.primary_language)
    try:
        result = provider.transcribe_file(
            file_path=Path(latest_media.storage_path),
            language_hint=language_hint,
        )
    except TranscriptionProviderError as exc:
        meeting.status = MeetingStatus.FAILED.value
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Transcription provider failed: {exc}",
        ) from exc

    persisted_segments: list[TranscriptSegment] = []
    for seg_data in result.segments:
        segment = TranscriptSegment(
            meeting_id=meeting.id,
            media_asset_id=latest_media.id,
            segment_index=seg_data.segment_index,
            start_time_seconds=seg_data.start_time_seconds,
            end_time_seconds=seg_data.end_time_seconds,
            text=seg_data.text,
            language=seg_data.language.value,
            speaker_label=seg_data.speaker_label,
            confidence=seg_data.confidence,
        )
        db.add(segment)
        persisted_segments.append(segment)

    meeting.status = MeetingStatus.COMPLETED.value
    db.commit()
    for seg in persisted_segments:
        db.refresh(seg)

    return build_transcript_response(meeting, persisted_segments)


@router.get("/{meeting_id}/transcript", response_model=MeetingTranscriptResponse)
def get_meeting_transcript(
    meeting_id: str,
    db: DbSession,
) -> MeetingTranscriptResponse:
    meeting = get_meeting_or_404(meeting_id, db)
    stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting.id)
        .order_by(TranscriptSegment.segment_index.asc())
    )
    segments = list(db.scalars(stmt).all())
    return build_transcript_response(meeting, segments)


@router.post(
    "/{meeting_id}/diarize",
    response_model=MeetingDiarizationResponse,
    status_code=status.HTTP_200_OK,
)
def diarize_meeting(
    meeting_id: str,
    db: DbSession,
    diarizer: DiarizerProvider,
    payload: DiarizeRequestPayload = None,
) -> MeetingDiarizationResponse:
    meeting = get_meeting_or_404(meeting_id, db)
    options = payload or DiarizeMeetingRequest()

    segments_stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting.id)
        .order_by(TranscriptSegment.segment_index.asc())
    )
    segments = list(db.scalars(segments_stmt).all())
    if not segments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot diarize a meeting before generating transcript segments.",
        )

    latest_media_stmt = (
        select(MediaAsset)
        .where(MediaAsset.meeting_id == meeting.id)
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    )
    latest_media = db.scalar(latest_media_stmt)
    if latest_media is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No media asset found for speaker diarization.",
        )

    try:
        diarization_result = diarizer.diarize_file(
            Path(latest_media.storage_path))
    except DiarizationProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Speaker diarization provider failed: {exc}",
        ) from exc

    windows = [
        SegmentTimeWindow(
            segment_id=seg.id,
            segment_index=seg.segment_index,
            start_time_seconds=seg.start_time_seconds,
            end_time_seconds=seg.end_time_seconds,
        )
        for seg in segments
    ]
    alignments = align_turns_with_segments(
        segments=windows,
        turns=diarization_result.turns,
        min_confidence_threshold=options.min_confidence_threshold,
    )
    alignment_by_id = {item.segment_id: item for item in alignments}

    overlap_by_segment_id: dict[str, bool] = {}
    low_confidence_count = 0
    overlapping_count = 0

    for seg in segments:
        aligned = alignment_by_id[seg.id]
        seg.speaker_label = aligned.speaker_label
        seg.confidence = aligned.confidence
        overlap_by_segment_id[seg.id] = aligned.is_overlapping
        if aligned.is_low_confidence:
            low_confidence_count += 1
        if aligned.is_overlapping:
            overlapping_count += 1

    db.commit()
    for seg in segments:
        db.refresh(seg)

    speakers = rebuild_meeting_speakers(
        meeting_id=meeting.id,
        segments=segments,
        overlap_by_segment_id=overlap_by_segment_id,
        db=db,
    )

    return MeetingDiarizationResponse(
        meeting_id=meeting.id,
        total_speakers=len(speakers),
        low_confidence_segments_count=low_confidence_count,
        overlapping_segments_count=overlapping_count,
        speakers=[SpeakerResponse.model_validate(s) for s in speakers],
        segments=[TranscriptSegmentResponse.model_validate(
            s) for s in segments],
    )


@router.get("/{meeting_id}/speakers", response_model=list[SpeakerResponse])
def list_meeting_speakers(
    meeting_id: str,
    db: DbSession,
) -> list[SpeakerResponse]:
    meeting = get_meeting_or_404(meeting_id, db)
    stmt = (
        select(Speaker)
        .where(Speaker.meeting_id == meeting.id)
        .order_by(Speaker.speaker_label.asc())
    )
    speakers = list(db.scalars(stmt).all())
    return [SpeakerResponse.model_validate(s) for s in speakers]


@router.patch(
    "/{meeting_id}/speakers/{speaker_label}",
    response_model=SpeakerResponse,
)
def update_meeting_speaker(
    meeting_id: str,
    speaker_label: str,
    payload: SpeakerUpdateRequest,
    db: DbSession,
) -> SpeakerResponse:
    meeting = get_meeting_or_404(meeting_id, db)
    stmt = select(Speaker).where(
        Speaker.meeting_id == meeting.id,
        Speaker.speaker_label == speaker_label,
    )
    speaker = db.scalar(stmt)
    if speaker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Speaker '{speaker_label}' not found in meeting '{meeting_id}'.",
        )

    target_label = payload.new_speaker_label or speaker_label

    if payload.new_speaker_label and payload.new_speaker_label != speaker_label:
        seg_stmt = select(TranscriptSegment).where(
            TranscriptSegment.meeting_id == meeting.id,
            TranscriptSegment.speaker_label == speaker_label,
        )
        for seg in db.scalars(seg_stmt).all():
            seg.speaker_label = target_label
        db.commit()

        all_segments = list(
            db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting.id)
                .order_by(TranscriptSegment.segment_index.asc())
            ).all()
        )
        rebuild_meeting_speakers(
            meeting_id=meeting.id,
            segments=all_segments,
            overlap_by_segment_id={},
            db=db,
        )
        target_stmt = select(Speaker).where(
            Speaker.meeting_id == meeting.id,
            Speaker.speaker_label == target_label,
        )
        speaker = db.scalar(target_stmt)
        if speaker is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Updated speaker '{target_label}' not found.",
            )

    if payload.display_name is not None:
        speaker.display_name = payload.display_name

    speaker.is_user_verified = True
    db.commit()
    db.refresh(speaker)
    return SpeakerResponse.model_validate(speaker)
