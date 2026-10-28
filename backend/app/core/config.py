"""Environment-driven application settings."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    # 127.0.0.1 rather than "localhost": published ports bind IPv4 loopback only, and some
    # hosts try IPv6 ::1 first and stall before falling back.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "cloudpulse"
    postgres_user: str = "cloudpulse"
    postgres_password: SecretStr = SecretStr("")

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_connect_timeout_seconds: int = 5

    # --- Monitoring ---------------------------------------------------------------
    # API + worker: bounds applied when resources are created/updated and checked.
    monitoring_min_interval_seconds: int = Field(default=10, ge=1)
    monitoring_max_interval_seconds: int = Field(default=86_400, ge=1)
    monitoring_default_interval_seconds: int = Field(default=60, ge=1)
    monitoring_default_timeout_seconds: int = Field(default=10, ge=1)
    monitoring_max_timeout_seconds: int = Field(default=30, ge=1)
    # Extra time beyond a check's timeout before its lease is considered abandoned.
    monitoring_lease_grace_seconds: int = Field(default=30, ge=1)
    # Hosts (or host:port) exempt from the private-address block. Development only.
    monitoring_allowed_local_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Worker only.
    monitoring_poll_interval_seconds: float = Field(default=2.0, gt=0)
    monitoring_max_concurrent_checks: int = Field(default=10, ge=1, le=200)
    monitoring_shutdown_grace_seconds: float = Field(default=20.0, ge=0)
    monitoring_heartbeat_file: str = "/tmp/cloudpulse-worker-heartbeat"

    # --- Metrics history ----------------------------------------------------------
    # Worker: retention. API: query limits.
    metrics_retention_days: int = Field(default=30, ge=1)
    metrics_cleanup_interval_hours: float = Field(default=24.0, gt=0)
    metrics_cleanup_batch_size: int = Field(default=1000, ge=1, le=100_000)
    metrics_max_query_days: int = Field(default=30, ge=1)
    metrics_default_window_hours: int = Field(default=24, ge=1)
    metrics_max_timeseries_buckets: int = Field(default=1000, ge=1, le=10_000)
    metrics_default_page_size: int = Field(default=50, ge=1)
    metrics_max_page_size: int = Field(default=200, ge=1, le=1000)

    @field_validator("monitoring_allowed_local_hosts", mode="before")
    @classmethod
    def _split_allowed_local_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip().lower() for host in value.split(",") if host.strip()]
        return value

    @model_validator(mode="after")
    def _validate_monitoring_bounds(self) -> "Settings":
        if self.monitoring_min_interval_seconds > self.monitoring_max_interval_seconds:
            raise ValueError("MONITORING_MIN_INTERVAL_SECONDS must be <= MAX_INTERVAL_SECONDS")
        if not (
            self.monitoring_min_interval_seconds
            <= self.monitoring_default_interval_seconds
            <= self.monitoring_max_interval_seconds
        ):
            raise ValueError("MONITORING_DEFAULT_INTERVAL_SECONDS must be within min/max interval")
        if self.monitoring_default_timeout_seconds > self.monitoring_max_timeout_seconds:
            raise ValueError("MONITORING_DEFAULT_TIMEOUT_SECONDS must be <= MAX_TIMEOUT_SECONDS")
        if self.monitoring_default_timeout_seconds > self.monitoring_default_interval_seconds:
            raise ValueError("MONITORING_DEFAULT_TIMEOUT_SECONDS must be <= default interval")
        if self.metrics_default_page_size > self.metrics_max_page_size:
            raise ValueError("METRICS_DEFAULT_PAGE_SIZE must be <= METRICS_MAX_PAGE_SIZE")
        if self.metrics_max_query_days > self.metrics_retention_days:
            # Queries wider than retention would silently describe data that no longer exists.
            raise ValueError("METRICS_MAX_QUERY_DAYS must be <= METRICS_RETENTION_DAYS")
        if self.metrics_default_window_hours > self.metrics_max_query_days * 24:
            raise ValueError("METRICS_DEFAULT_WINDOW_HOURS must fit within METRICS_MAX_QUERY_DAYS")
        if self.monitoring_allowed_local_hosts and self.environment in {"staging", "production"}:
            # Fail closed: the SSRF relaxation can never be active in a deployed environment.
            raise ValueError(
                "MONITORING_ALLOWED_LOCAL_HOSTS is a development-only setting and is not "
                f"permitted when ENVIRONMENT={self.environment}"
            )
        return self

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
