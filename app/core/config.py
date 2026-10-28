import contextlib
import json
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LanguageCode(StrEnum):
    PERSIAN = "fa"
    ENGLISH = "en"
    MIXED = "mixed"


class Settings(BaseSettings):
    app_name: str = "AI Meeting Assistant"
    app_env: str = "development"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    default_meeting_language: LanguageCode = LanguageCode.MIXED
    supported_languages: Any = [
        LanguageCode.PERSIAN,
        LanguageCode.ENGLISH,
        LanguageCode.MIXED,
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("supported_languages", mode="before")
    @classmethod
    def parse_supported_languages(cls, value: Any) -> list[LanguageCode]:
        if isinstance(value, str):
            val = value.strip()
            if val.startswith("[") and val.endswith("]"):
                with contextlib.suppress(json.JSONDecodeError, ValueError):
                    items = json.loads(val)
                    if isinstance(items, list):
                        return [LanguageCode(item) for item in items]
            items = [item.strip() for item in val.split(",") if item.strip()]
            return [LanguageCode(item) for item in items]
        return [LanguageCode(item) for item in value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
