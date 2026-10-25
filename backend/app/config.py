"""Application configuration.

Values come from environment variables (prefix ``YTC_``) or a ``.env`` file at the repository root.
Secrets for providers are *never* given defaults and are only read through ``Settings``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_prefix="YTC_",
        extra="ignore",
    )

    env: Literal["development", "production", "test"] = "development"

    # --- storage -----------------------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    database_url: str | None = None

    # --- network (localhost only by default) ---------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    allowed_hosts: str = "localhost,127.0.0.1,[::1]"  # Host headers the API will answer to

    # --- sessions / security -------------------------------------------------------------------
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # set true when serving over HTTPS
    login_max_failures: int = 5
    login_window_seconds: int = 300
    login_lockout_seconds: int = 900
    api_rate_limit_per_minute: int = 600

    # --- worker --------------------------------------------------------------------------------
    worker_poll_seconds: float = 2.0
    task_lease_seconds: int = 300
    task_max_attempts: int = 3
    task_backoff_base_seconds: int = 30
    task_backoff_cap_seconds: int = 3600
    max_concurrent_tasks: int = 2

    # --- backups -------------------------------------------------------------------------------
    backup_retention_count: int = 14

    # --- provider credentials (placeholders only in .env.example; used from later phases) ------
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    elevenlabs_api_key: SecretStr | None = Field(default=None, validation_alias="ELEVENLABS_API_KEY")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_dir = self.data_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(db_dir / 'company.sqlite3').as_posix()}"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def secret_values(self) -> list[str]:
        """All configured secret strings, used by the redactor."""
        out: list[str] = []
        for name in ("anthropic_api_key", "elevenlabs_api_key"):
            v = getattr(self, name)
            if v is not None and v.get_secret_value():
                out.append(v.get_secret_value())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
