import os

import pytest
from sqlalchemy import make_url

from app.core.config import Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_database_url_built_from_components() -> None:
    settings = make_settings(
        postgres_host="db",
        postgres_port=5433,
        postgres_db="pulse",
        postgres_user="svc",
        postgres_password="p@ss:word/1",
    )
    url = make_url(settings.database_url)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("db", 5433, "pulse", "svc")
    assert url.password == "p@ss:word/1"


def test_ambient_database_url_does_not_leak_into_tests() -> None:
    # Guards the isolate_settings_env fixture: a DATABASE_URL present in the host/CI
    # environment must not override component-based settings.
    assert "DATABASE_URL" not in os.environ
    assert make_url(make_settings(postgres_host="db").database_url).host == "db"


def test_database_url_env_var_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@elsewhere:5432/other")
    settings = make_settings(postgres_host="db")
    assert make_url(settings.database_url).host == "elsewhere"


def test_blank_database_url_falls_back_to_components(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    settings = make_settings(postgres_host="db")
    assert make_url(settings.database_url).host == "db"


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test ,")
    assert make_settings().cors_origins == ["http://a.test", "http://b.test"]


def test_password_is_not_exposed_in_repr() -> None:
    settings = make_settings(postgres_password="super-secret")
    assert "super-secret" not in repr(settings)


def test_log_level_is_normalized() -> None:
    assert make_settings(log_level="debug").log_level == "DEBUG"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "postgres_password": "x" * 20}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("password", ["", "change-me", "short", "Password", "postgres"])
def test_deployed_environments_reject_placeholder_database_passwords(
    environment: str, password: str
) -> None:
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        _settings(environment=environment, postgres_password=password)


def test_deployed_environments_accept_a_generated_password() -> None:
    assert _settings(environment="production", postgres_password="Zx3-k9QpLm2_vT8wRa7").environment


def test_local_and_test_environments_keep_simple_passwords() -> None:
    assert _settings(environment="local", postgres_password="change-me")
    assert _settings(environment="test", postgres_password="")


def test_an_explicit_database_url_is_not_second_guessed() -> None:
    settings = _settings(
        environment="production", DATABASE_URL="postgresql+psycopg://u:p@db:5432/x"
    )
    assert settings.database_url_override is not None


def test_api_docs_are_on_by_default_and_can_be_disabled() -> None:
    assert _settings().api_docs_enabled is True
    assert _settings(api_docs_enabled=False).api_docs_enabled is False
