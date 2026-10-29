from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


def make_valid_wav_bytes(payload_size: int = 32) -> bytes:
    # Minimal valid RIFF/WAVE header (44 bytes) + PCM silence payload
    total_data = payload_size
    riff_size = 36 + total_data
    return (
        b"RIFF"
        + riff_size.to_bytes(4, "little")
        + b"WAVE"
        + b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + total_data.to_bytes(4, "little")
        + (b"\x00" * total_data)
    )


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    testing_session_local = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=test_engine)

    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    def override_get_settings() -> Settings:
        return Settings(
            media_storage_dir=str(tmp_path / "media"),
            max_upload_size_bytes=1024,
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings

    with TestClient(app) as test_client:
        yield test_client

    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def test_upload_valid_wav_updates_meeting_status(client: TestClient) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "جلسه تست آپلود صوت", "primary_language": "fa"},
    ).json()
    meeting_id = meeting["id"]

    wav_bytes = make_valid_wav_bytes(64)
    upload_response = client.post(
        f"/api/v1/meetings/{meeting_id}/media",
        files={"file": ("tehran_meeting.wav", wav_bytes, "audio/wav")},
    )

    assert upload_response.status_code == 201
    asset = upload_response.json()
    assert asset["meeting_id"] == meeting_id
    assert asset["original_filename"] == "tehran_meeting.wav"
    assert asset["file_extension"] == ".wav"
    assert asset["file_size_bytes"] == len(wav_bytes)
    assert len(asset["sha256_checksum"]) == 64

    status_response = client.get(f"/api/v1/meetings/{meeting_id}/status")
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["status"] == "media_uploaded"
    assert status_data["media_assets_count"] == 1
    assert status_data["latest_media_asset"]["id"] == asset["id"]


def test_upload_rejects_fake_wav_with_invalid_magic_bytes(client: TestClient) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Security Check Meeting", "primary_language": "en"},
    ).json()

    fake_wav = b"This is plain text disguised as a WAV audio file."
    response = client.post(
        f"/api/v1/meetings/{meeting['id']}/media",
        files={"file": ("malicious.wav", fake_wav, "audio/wav")},
    )

    assert response.status_code == 400
    assert "content does not match" in response.json()["detail"]


def test_upload_rejects_unsupported_extension(client: TestClient) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Extension Check", "primary_language": "en"},
    ).json()

    response = client.post(
        f"/api/v1/meetings/{meeting['id']}/media",
        files={"file": ("script.exe", b"MZ\x90\x00\x03\x00\x00\x00",
                        "application/octet-stream")},
    )

    assert response.status_code == 415


def test_upload_rejects_oversized_file(client: TestClient) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Size Limit Check", "primary_language": "mixed"},
    ).json()

    # Fixture max_upload_size_bytes is 1024 bytes; generate > 1200 bytes
    oversized_wav = make_valid_wav_bytes(1200)
    response = client.post(
        f"/api/v1/meetings/{meeting['id']}/media",
        files={"file": ("large_recording.wav", oversized_wav, "audio/wav")},
    )

    assert response.status_code == 413


def test_upload_to_nonexistent_meeting_returns_404(client: TestClient) -> None:
    wav_bytes = make_valid_wav_bytes(32)
    response = client.post(
        "/api/v1/meetings/missing-meeting-id/media",
        files={"file": ("sample.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 404
