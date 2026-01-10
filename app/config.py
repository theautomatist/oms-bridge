from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    lobaro_base_url: str = "https://platform.lobaro.com"
    lobaro_token: str = ""
    lobaro_timeout_s: float = 10.0

    keys_db_path: str = "./keys.db"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
