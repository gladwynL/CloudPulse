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
