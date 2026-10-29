from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.config import LanguageCode
from app.models.meeting import MeetingStatus


class MediaAssetResponse(BaseModel):
    id: str
    meeting_id: str
    original_filename: str
    content_type: str
    file_extension: str
    file_size_bytes: int
    sha256_checksum: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeetingStatusResponse(BaseModel):
    meeting_id: str
    status: MeetingStatus
    primary_language: LanguageCode
    output_language: LanguageCode
    media_assets_count: int
    latest_media_asset: MediaAssetResponse | None = None
    updated_at: datetime
