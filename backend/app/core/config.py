"""Environment-driven application settings."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Application settings loaded from environment variables (and an optional .env file).

    The database connection can be supplied either as a full ``DATABASE_URL`` or as
    individual ``POSTGRES_*`` components. ``DATABASE_URL`` wins when both are set.
    """

    model_config = SettingsConfigDict(
        # The repo-root .env is shared with Docker Compose; a backend/.env may override it.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CloudPulse API"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "development", "staging", "production"] = "local"

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )

    database_url_override: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cloudpulse"
    postgres_user: str = "cloudpulse"
    postgres_password: SecretStr = SecretStr("")

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_connect_timeout_seconds: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url_override", mode="before")
    @classmethod
    def _blank_database_url_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection URL.

        A plain property (not a computed field) so the credential-bearing URL never
        appears in ``repr()`` or ``model_dump()``. Never log it.
        """
        if self.database_url_override is not None:
            return self.database_url_override.get_secret_value()
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value() or None,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
