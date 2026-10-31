from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI-Merge-Assistant"
    environment: str = "development"
    database_url: str = "sqlite:///./ai_merge_i0.db"
    redis_url: str = "redis://localhost:6379/0"

    # Явный флаг демо/оффлайн режима (по умолчанию ВЫКЛЮЧЕН)
    offline_demo_mode: bool = False

    token_encryption_key: str = "dev-only-32-byte-secret-key-0000"
    session_secret_key: str = "dev-only-session-secret-key-0000"
    github_webhook_secret: str = "dev-only-webhook-secret"

    artifacts_volume_path: str = "/tmp/ai_merge_artifacts"


settings = Settings()
