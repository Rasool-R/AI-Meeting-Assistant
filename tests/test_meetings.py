from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
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

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def test_create_persian_meeting_defaults_output_language_to_persian(client: TestClient) -> None:
    response = client.post(
        "/api/v1/meetings",
        json={
            "title": "جلسه بررسی معماری سیستم",
            "description": "بررسی نیازمندی‌های پیاده‌سازی صدای فارسی و انگلیسی",
            "primary_language": "fa",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"]
    assert data["title"] == "جلسه بررسی معماری سیستم"
    assert data["primary_language"] == "fa"
    assert data["output_language"] == "fa"
    assert data["status"] == "created"


def test_create_mixed_meeting_with_explicit_english_output(client: TestClient) -> None:
    response = client.post(
        "/api/v1/meetings",
        json={
            "title": "Sprint Planning - اسپرینت چهارم",
            "primary_language": "mixed",
            "output_language": "en",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["primary_language"] == "mixed"
    assert data["output_language"] == "en"


def test_list_and_get_meeting_by_id(client: TestClient) -> None:
    first = client.post(
        "/api/v1/meetings",
        json={"title": "First Meeting", "primary_language": "en"},
    ).json()
    second = client.post(
        "/api/v1/meetings",
        json={"title": "جلسه دوم", "primary_language": "fa"},
    ).json()

    list_response = client.get("/api/v1/meetings")
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["total"] == 2
    assert len(list_data["items"]) == 2
    returned_ids = {item["id"] for item in list_data["items"]}
    assert returned_ids == {first["id"], second["id"]}

    detail_response = client.get(f"/api/v1/meetings/{second['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["title"] == "جلسه دوم"


def test_get_nonexistent_meeting_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/meetings/non-existent-id")
    assert response.status_code == 404


def test_create_meeting_rejects_blank_title(client: TestClient) -> None:
    response = client.post(
        "/api/v1/meetings",
        json={"title": "   ", "primary_language": "fa"},
    )
    assert response.status_code == 422
