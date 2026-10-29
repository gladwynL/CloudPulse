"""Tests for the deployment tooling (scripts/) and the deployment configuration files.

Nothing here needs Docker: the scripts' subprocess layer is replaced by fakes, and the Compose
files are checked as text. The real Docker round trips (backup, isolated verify, restore into a
new database) are exercised by the CI docker job and the manual verification in the README.
"""

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_backup = load_script("db_backup")
init_env = load_script("init_env")
check_ports = load_script("check_ports")


def completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# --------------------------------------------------------------------------------------------
# db_backup
# --------------------------------------------------------------------------------------------


class FakeDb:
    """Stands in for `docker compose exec db ...` during backup/restore tests."""

    def __init__(self, dump: bytes = b"PGDMP-fake", dump_rc: int = 0, list_rc: int = 0) -> None:
        self.dump, self.dump_rc, self.list_rc = dump, dump_rc, list_rc
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, stdin: object = None, stdout: object = subprocess.PIPE):
        self.calls.append(args)
        if args[0] == "pg_dump":
            if self.dump_rc == 0 and hasattr(stdout, "write"):
                stdout.write(self.dump)  # type: ignore[union-attr]
            return completed(self.dump_rc, stderr=b"dump exploded" if self.dump_rc else b"")
        if args[0] == "pg_restore" and "--list" in args:
            return completed(self.list_rc, stderr=b"not an archive" if self.list_rc else b"")
        return completed()


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_backup, "compose_exec", fake)
    monkeypatch.setattr(db_backup, "db_settings", lambda: ("cloudpulse", "cloudpulse"))
    return fake


def test_backup_filename_is_timestamped_and_safe() -> None:
    now = dt.datetime(2026, 10, 28, 9, 5, 7, tzinfo=dt.UTC)
    path = db_backup.backup_path(Path("backups"), "cloudpulse", now)
    assert path.name == "cloudpulse-cloudpulse-20261028T090507Z.dump"


@pytest.mark.parametrize("name", ["cloudpulse_recovered", "_scratch1", "a"])
def test_database_names_that_are_safe_are_accepted(name: str) -> None:
    assert db_backup.validate_database_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "Upper", "1starts_with_digit", "has-dash", "x;drop database y", 'q"uote', "a" * 64]
)
def test_unsafe_database_names_are_refused(name: str) -> None:
    with pytest.raises(db_backup.Refused):
        db_backup.validate_database_name(name)


def test_backup_writes_a_checksummed_file_and_never_a_password(
    tmp_path: Path, fake_db: FakeDb, capsys: pytest.CaptureFixture[str]
) -> None:
    target = db_backup.backup(tmp_path / "out")
    assert target.read_bytes() == b"PGDMP-fake"
    sidecar = target.with_name(target.name + ".sha256")
    assert sidecar.read_text().split()[0] == db_backup.sha256_of(target)
    # The archive was validated before being reported as good.
    assert any("--list" in call for call in fake_db.calls)
    # No credential ever appears on a command line or in the output.
    assert not any("PASSWORD" in part.upper() for call in fake_db.calls for part in call)
    assert "password" not in capsys.readouterr().out.lower()


def test_backup_refuses_to_overwrite_an_existing_file(
    tmp_path: Path, fake_db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "cloudpulse-cloudpulse-fixed.dump"
    existing.write_bytes(b"precious")
    monkeypatch.setattr(db_backup, "backup_path", lambda *_a, **_k: existing)
    with pytest.raises(db_backup.Refused, match="overwrite"):
        db_backup.backup(tmp_path)
    assert existing.read_bytes() == b"precious"


def test_a_failed_dump_leaves_no_partial_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_backup, "compose_exec", FakeDb(dump_rc=1))
    monkeypatch.setattr(db_backup, "db_settings", lambda: ("u", "d"))
    with pytest.raises(db_backup.Failed, match="pg_dump failed"):
        db_backup.backup(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_dump_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_backup, "compose_exec", FakeDb(dump=b""))
    monkeypatch.setattr(db_backup, "db_settings", lambda: ("u", "d"))
    with pytest.raises(db_backup.Failed, match="empty"):
        db_backup.backup(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_unreadable_archive_is_not_reported_as_a_good_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_backup, "compose_exec", FakeDb(list_rc=1))
    monkeypatch.setattr(db_backup, "db_settings", lambda: ("u", "d"))
    with pytest.raises(db_backup.Failed, match="not a readable archive"):
        db_backup.backup(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_damaged_backup_is_detected_by_its_checksum(tmp_path: Path) -> None:
    dump = tmp_path / "b.dump"
    dump.write_bytes(b"original")
    (tmp_path / "b.dump.sha256").write_text(f"{db_backup.sha256_of(dump)}  b.dump\n")
    db_backup.check_sidecar(dump)  # intact: no error
    dump.write_bytes(b"corrupted")
    with pytest.raises(db_backup.Failed, match="damaged"):
        db_backup.check_sidecar(dump)


class RestoreScript:
    """Fake `psql` for restore tests: answers the three queries the script asks."""

    def __init__(self, existing_database: bool, tables: int) -> None:
        self.existing_database, self.tables = existing_database, tables
        self.executed: list[str] = []

    def __call__(self, _user: str, _database: str, sql: str) -> str:
        self.executed.append(sql)
        if "pg_database" in sql:
            return "1" if self.existing_database else ""
        if "information_schema.tables" in sql:
            return str(self.tables)
        return "0"


@pytest.fixture
def dump_file(tmp_path: Path) -> Path:
    path = tmp_path / "b.dump"
    path.write_bytes(b"PGDMP")
    return path


def patch_restore(monkeypatch: pytest.MonkeyPatch, script: RestoreScript) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_backup, "compose_exec", fake)
    monkeypatch.setattr(db_backup, "db_settings", lambda: ("cloudpulse", "cloudpulse"))
    monkeypatch.setattr(db_backup, "psql", script)
    return fake


def test_restore_refuses_the_live_database_name_by_default(
    monkeypatch: pytest.MonkeyPatch, dump_file: Path
) -> None:
    fake = patch_restore(monkeypatch, RestoreScript(True, 6))
    with pytest.raises(db_backup.Refused, match="live database"):
        db_backup.restore(dump_file, "cloudpulse")
    assert not any(call[0] == "pg_restore" for call in fake.calls)


def test_restore_refuses_any_database_that_already_has_tables(
    monkeypatch: pytest.MonkeyPatch, dump_file: Path
) -> None:
    fake = patch_restore(monkeypatch, RestoreScript(True, 6))
    for name, flag in (("cloudpulse_other", False), ("cloudpulse", True)):
        with pytest.raises(db_backup.Refused, match="already contains 6 tables"):
            db_backup.restore(dump_file, name, allow_live_name=flag)
    assert not any(call[0] == "pg_restore" for call in fake.calls)


def test_restore_into_a_new_database_creates_it_then_restores(
    monkeypatch: pytest.MonkeyPatch, dump_file: Path
) -> None:
    script = RestoreScript(existing_database=False, tables=0)
    fake = patch_restore(monkeypatch, script)
    db_backup.restore(dump_file, "cloudpulse_recovered")
    assert any('create database "cloudpulse_recovered"' in sql for sql in script.executed)
    restore_call = next(call for call in fake.calls if call[0] == "pg_restore")
    assert "--exit-on-error" in restore_call and "cloudpulse_recovered" in restore_call


def test_restore_into_the_empty_live_database_needs_the_explicit_flag(
    monkeypatch: pytest.MonkeyPatch, dump_file: Path
) -> None:
    fake = patch_restore(monkeypatch, RestoreScript(existing_database=True, tables=0))
    db_backup.restore(dump_file, "cloudpulse", allow_live_name=True)
    assert any(call[0] == "pg_restore" for call in fake.calls)


def test_cli_maps_outcomes_to_distinct_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise db_backup.Refused("no")

    def fail(*_a: object, **_k: object) -> None:
        raise db_backup.Failed("boom")

    def bad(*_a: object, **_k: object) -> None:
        raise db_backup._BadBackup("empty")

    monkeypatch.setattr(db_backup, "backup", refuse)
    assert db_backup.main(["backup"]) == db_backup.EXIT_REFUSED
    monkeypatch.setattr(db_backup, "backup", fail)
    assert db_backup.main(["backup"]) == db_backup.EXIT_FAILED
    monkeypatch.setattr(db_backup, "verify", bad)
    assert db_backup.main(["verify", str(tmp_path / "x")]) == db_backup.EXIT_BAD_BACKUP
    monkeypatch.setattr(db_backup, "backup", lambda *_a: None)
    assert db_backup.main(["backup"]) == db_backup.EXIT_OK
    assert "REFUSED" in capsys.readouterr().err


def test_verify_requires_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(db_backup.Refused, match="not found"):
        db_backup.verify(tmp_path / "missing.dump")


# --------------------------------------------------------------------------------------------
# init_env
# --------------------------------------------------------------------------------------------


def test_init_env_replaces_only_the_password_line() -> None:
    template = "A=1\nPOSTGRES_PASSWORD=change-me\nB=2\n"
    rendered = init_env.render(template, "s3cret-value")
    assert rendered == "A=1\nPOSTGRES_PASSWORD=s3cret-value\nB=2\n"


def test_init_env_generates_a_strong_password_and_does_not_print_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".env"
    assert init_env.main(["--output", str(out)]) == 0
    text = out.read_text()
    password = re.search(r"^POSTGRES_PASSWORD=(.*)$", text, re.MULTILINE).group(1)  # type: ignore[union-attr]
    assert len(password) >= 24 and password.lower() not in init_env.WEAK
    assert password not in capsys.readouterr().out
    assert init_env.password_problem(text) is None


def test_init_env_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    out = tmp_path / ".env"
    out.write_text("POSTGRES_PASSWORD=mine-do-not-touch\n")
    assert init_env.main(["--output", str(out)]) == 2
    assert out.read_text() == "POSTGRES_PASSWORD=mine-do-not-touch\n"


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("POSTGRES_PASSWORD=change-me\n", "placeholder"),
        ("POSTGRES_PASSWORD=\n", "placeholder"),
        ("POSTGRES_PASSWORD=short\n", "shorter"),
        ("OTHER=1\n", "not set"),
    ],
)
def test_init_env_check_flags_weak_passwords(text: str, problem: str) -> None:
    assert problem in (init_env.password_problem(text) or "")


# --------------------------------------------------------------------------------------------
# check_ports
# --------------------------------------------------------------------------------------------


def publisher(url: str, port: int) -> dict[str, object]:
    return {"URL": url, "PublishedPort": port, "TargetPort": 80}


def test_port_audit_accepts_loopback_only() -> None:
    assert check_ports.audit({"frontend": [publisher("127.0.0.1", 3000)], "db": []}) == []


def test_port_audit_flags_a_wildcard_binding() -> None:
    problems = check_ports.audit({"db": [publisher("0.0.0.0", 5432)]})
    assert problems and "not loopback" in problems[0]


def test_port_audit_can_restrict_publishing_to_named_services() -> None:
    published = {
        "frontend": [publisher("127.0.0.1", 3000)],
        "backend": [publisher("127.0.0.1", 8000)],
    }
    assert check_ports.audit(published, {"frontend", "backend"}) == []
    problems = check_ports.audit(published, {"frontend"})
    assert len(problems) == 1 and problems[0].startswith("backend")


def test_port_audit_ignores_unpublished_ports() -> None:
    assert (
        check_ports.audit({"worker": [{"URL": "", "PublishedPort": 0, "TargetPort": 8000}]}) == []
    )


# --------------------------------------------------------------------------------------------
# Deployment configuration files
# --------------------------------------------------------------------------------------------


def test_every_published_port_in_the_base_compose_file_is_loopback_only() -> None:
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    ports = re.findall(r'^\s+- "(.*:\d+)"$', text, re.MULTILINE)
    assert ports, "expected the compose file to publish some ports"
    assert all(p.startswith("127.0.0.1:") for p in ports), ports


def test_the_hardened_override_resets_backend_and_database_ports() -> None:
    text = (REPO / "docker-compose.hardened.yml").read_text(encoding="utf-8")
    for service in ("db", "backend"):
        block = re.search(rf"^  {service}:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL)
        assert block and "ports: !reset []" in block.group(1), service
    assert "same-origin" in text and 'API_DOCS_ENABLED: "false"' in text
    assert text.count("security_opt:") == 1  # defined once, shared through the YAML anchor
    assert not re.search(r"^\s*privileged:", text, re.MULTILINE)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_hardened_compose_config_merges_to_a_single_published_port() -> None:
    result = subprocess.run(
        [
            "docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.hardened.yml",
            "--profile", "demo", "config", "--format", "json",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRES_PASSWORD": "x" * 16},
        check=False,
    )  # fmt: skip
    if result.returncode != 0:
        pytest.skip(f"docker compose unavailable: {result.stderr[:200]}")
    services = json.loads(result.stdout)["services"]
    publishing = {name for name, svc in services.items() if svc.get("ports")}
    assert publishing == {"frontend"}
    for name, svc in services.items():
        assert svc.get("read_only") is True, name
        assert "ALL" in svc.get("cap_drop", []), name
        assert not svc.get("privileged"), name


def test_dockerfiles_run_as_non_root_and_embed_no_secrets() -> None:
    backend = (REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER app$", backend, re.MULTILINE)
    for dockerfile in (REPO / "backend" / "Dockerfile", REPO / "frontend" / "Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8")
        assert not re.search(r"(PASSWORD|SECRET|TOKEN|API_KEY)\s*=\s*\S", text), dockerfile
    frontend = (REPO / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "nginx-unprivileged" in frontend


def test_env_example_contains_no_real_credentials() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    password = re.search(r"^POSTGRES_PASSWORD=(.*)$", text, re.MULTILINE)
    assert password and password.group(1).strip() in init_env.WEAK
    assert not re.search(r"(?i)(ghp_|github_pat_|AKIA[0-9A-Z]{12}|BEGIN [A-Z ]*PRIVATE KEY)", text)


def test_local_secrets_and_backups_are_not_tracked_or_ignored_incorrectly() -> None:
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", ignore, re.MULTILINE)
    assert re.search(r"^backups/$", ignore, re.MULTILINE)
    assert re.search(r"^\*\.dump$", ignore, re.MULTILINE)
    if shutil.which("git") and (REPO / ".git").exists():
        tracked = subprocess.run(
            ["git", "ls-files", ".env", "backups"], cwd=REPO, capture_output=True, text=True
        ).stdout.split()
        assert tracked == []
