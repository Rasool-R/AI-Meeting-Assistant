from fastapi.testclient import TestClient

from app.core.config import LanguageCode, Settings
from app.main import create_app


def test_health_endpoint_returns_ok_and_bilingual_support() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "AI Meeting Assistant"
    assert payload["default_language"] == "mixed"
    assert payload["supported_languages"] == ["fa", "en", "mixed"]


def test_settings_parses_comma_separated_languages() -> None:
    settings = Settings(
        SUPPORTED_LANGUAGES="fa,en",
        DEFAULT_MEETING_LANGUAGE="fa",
    )
    assert settings.default_meeting_language == LanguageCode.PERSIAN
    assert settings.supported_languages == [
        LanguageCode.PERSIAN, LanguageCode.ENGLISH]
