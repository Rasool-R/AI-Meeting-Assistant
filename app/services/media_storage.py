import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePath
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import Settings

ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    ".wav": {"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"},
    ".mp3": {"audio/mpeg", "audio/mp3", "application/octet-stream"},
    ".mp4": {"video/mp4", "audio/mp4", "application/octet-stream"},
    ".m4a": {"audio/mp4", "audio/x-m4a", "audio/m4a", "application/octet-stream"},
    ".ogg": {"audio/ogg", "video/ogg", "application/ogg", "application/octet-stream"},
    ".webm": {"audio/webm", "video/webm", "application/octet-stream"},
    ".flac": {"audio/flac", "audio/x-flac", "application/octet-stream"},
}

CHUNK_SIZE_BYTES = 64 * 1024


@dataclass(frozen=True)
class StoredMediaFile:
    original_filename: str
    stored_filename: str
    storage_path: str
    content_type: str
    file_extension: str
    file_size_bytes: int
    sha256_checksum: str


def sanitize_original_filename(filename: str | None) -> tuple[str, str]:
    if not filename or not filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must have a valid filename.",
        )
    safe_name = PurePath(filename.strip()).name
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename.",
        )
    extension = PurePath(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed_list = ", ".join(sorted(ALLOWED_EXTENSIONS.keys()))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file extension '{extension}'. Allowed: {allowed_list}",
        )
    return safe_name, extension


def has_valid_media_signature(extension: str, header: bytes) -> bool:
    if len(header) < 12:
        return False
    if extension == ".wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if extension == ".mp3":
        return header.startswith(b"ID3") or header[:2] in {
            b"\xff\xfb",
            b"\xff\xf3",
            b"\xff\xf2",
            b"\xff\xe3",
        }
    if extension == ".ogg":
        return header.startswith(b"OggS")
    if extension == ".flac":
        return header.startswith(b"fLaC")
    if extension == ".webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension in {".mp4", ".m4a"}:
        return header[4:8] == b"ftyp"
    return False


def store_validated_media_file(
    meeting_id: str,
    upload_file: UploadFile,
    settings: Settings,
) -> StoredMediaFile:
    original_filename, extension = sanitize_original_filename(
        upload_file.filename)
    content_type = (
        upload_file.content_type or "application/octet-stream").lower()

    if content_type not in ALLOWED_EXTENSIONS[extension]:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content-Type '{content_type}' is not valid for '{extension}' files.",
        )

    base_dir = Path(settings.media_storage_dir).resolve()
    meeting_dir = (base_dir / meeting_id).resolve()
    if not meeting_dir.is_relative_to(base_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid meeting storage path.",
        )
    meeting_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{uuid4()}{extension}"
    destination = (meeting_dir / stored_filename).resolve()
    if not destination.is_relative_to(meeting_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid destination file path.",
        )

    sha256 = hashlib.sha256()
    total_bytes = 0
    header_bytes = bytearray()

    try:
        with destination.open("wb") as out_file:
            while True:
                chunk = upload_file.file.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                if len(header_bytes) < 16:
                    needed = 16 - len(header_bytes)
                    header_bytes.extend(chunk[:needed])
                total_bytes += len(chunk)
                if total_bytes > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"File exceeds maximum allowed size of "
                            f"{settings.max_upload_size_bytes} bytes."
                        ),
                    )
                sha256.update(chunk)
                out_file.write(chunk)
    except HTTPException:
        if destination.exists():
            destination.unlink()
        raise

    if total_bytes == 0:
        if destination.exists():
            destination.unlink()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded media file is empty.",
        )

    if not has_valid_media_signature(extension, bytes(header_bytes)):
        if destination.exists():
            destination.unlink()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file content does not match the declared media format.",
        )

    return StoredMediaFile(
        original_filename=original_filename,
        stored_filename=stored_filename,
        storage_path=str(destination),
        content_type=content_type,
        file_extension=extension,
        file_size_bytes=total_bytes,
        sha256_checksum=sha256.hexdigest(),
    )
