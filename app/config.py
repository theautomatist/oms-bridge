from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    lobaro_token: str = ""
    lobaro_timeout_s: float = 10.0

    mqtt_url: str | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_template: str | None = None
    mqtt_qos: int = 1
    mqtt_retain: bool = False

    keys_db_path: str = "./keys.db"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
