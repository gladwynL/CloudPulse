#!/usr/bin/env python3
"""Back up, verify and restore CloudPulse's PostgreSQL database (Docker Compose, stdlib only).

    python scripts/db_backup.py backup                      # -> backups/cloudpulse-<db>-<UTC>.dump
    python scripts/db_backup.py verify backups/<file>.dump  # restore into a disposable container
    python scripts/db_backup.py restore backups/<file>.dump --database cloudpulse_recovered

Safety properties (each is covered by tests in backend/tests/test_deploy_scripts.py):

* No password is ever passed or printed. ``pg_dump``/``pg_restore``/``psql`` run *inside* the
  ``db`` container over its local socket, so no credentials leave the container.
* A backup is written to a new file and never overwrites an existing one; it is checked with
  ``pg_restore --list`` before it is reported as good, gets owner-only permissions where the OS
  supports them, and a ``.sha256`` sidecar. A failed backup leaves no partial file behind.
* ``verify`` restores into a throw-away PostgreSQL container with no volume and no published port,
  checks the expected tables and row counts, then removes the container. It never touches the
  running database.
* ``restore`` refuses to write into a database that already contains tables, and refuses the
  live database name unless you also pass ``--allow-live-database-name`` (and it is empty, as it
  is on a freshly created stack). It cannot overwrite existing data.

Compose settings are honoured the usual way (``COMPOSE_FILE``, ``COMPOSE_PROJECT_NAME`` ...), so
the same script works for the development and the hardened stacks.

Exit codes: 0 success; 1 operational failure; 2 refused for safety or bad usage; 3 verification
found a problem with the backup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = REPO_ROOT / "backups"
DB_SERVICE = "db"
# Keep in sync with the `db` image in docker-compose.yml (a dump restores into the same major).
VERIFY_IMAGE = "postgres:17-alpine"

EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_BAD_BACKUP = 0, 1, 2, 3

# Tables a usable CloudPulse backup must contain.
EXPECTED_TABLES = (
    "alembic_version",
    "monitored_resources",
    "resource_check_results",
    "alert_rules",
    "incidents",
    "incident_events",
)

DATABASE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Refused(Exception):
    """A safety rule stopped the operation (exit code 2)."""


class Failed(Exception):
    """An operation failed (exit code 1)."""


# --------------------------------------------------------------------------------------------
# Process helpers (tests replace `run` to avoid needing Docker)
# --------------------------------------------------------------------------------------------


def run(
    args: Sequence[str],
    *,
    stdin: object = None,
    stdout: object = subprocess.PIPE,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed argument lists, no shell
        list(args),
        stdin=stdin,  # type: ignore[arg-type]
        stdout=stdout,  # type: ignore[arg-type]
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )


def compose_exec(*args: str, stdin: object = None, stdout: object = subprocess.PIPE):
    return run(["docker", "compose", "exec", "-T", DB_SERVICE, *args], stdin=stdin, stdout=stdout)


def _text(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stdout or b"").decode("utf-8", "replace").strip()


def _error(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stderr or b"").decode("utf-8", "replace").strip() or "no error output"


def db_settings() -> tuple[str, str]:
    """The live database's user and name, read from the running db container."""
    values = []
    for variable in ("POSTGRES_USER", "POSTGRES_DB"):
        result = compose_exec("printenv", variable)
        if result.returncode != 0 or not _text(result):
            raise Failed(
                "cannot read the database settings from the 'db' container; is the stack running? "
                f"({_error(result)})"
            )
        values.append(_text(result))
    return values[0], values[1]


def psql(user: str, database: str, sql: str) -> str:
    result = compose_exec("psql", "-U", user, "-d", database, "-v", "ON_ERROR_STOP=1", "-tAc", sql)
    if result.returncode != 0:
        raise Failed(f"psql failed: {_error(result)}")
    return _text(result)


# --------------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------------


def backup_path(directory: Path, database: str, now: dt.datetime | None = None) -> Path:
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"cloudpulse-{database}-{stamp}.dump"


def validate_database_name(name: str) -> str:
    if not DATABASE_NAME.fullmatch(name):
        raise Refused(
            f"'{name}' is not a safe database name (lowercase letters, digits and underscores, "
            "starting with a letter or underscore, at most 63 characters)"
        )
    return name


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_sidecar(path: Path) -> None:
    """If a .sha256 sidecar exists the file must match it (detects a truncated/corrupted copy)."""
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        return
    recorded = sidecar.read_text(encoding="utf-8").split()[0]
    if recorded != sha256_of(path):
        raise Failed(f"{path.name} does not match its recorded checksum; the file is damaged")


# --------------------------------------------------------------------------------------------
# backup
# --------------------------------------------------------------------------------------------


def backup(directory: Path) -> Path:
    user, database = db_settings()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise Failed(f"cannot create the backup directory {directory}: {error}") from error
    with_posix_perms(lambda: os.chmod(directory, 0o700))
    target = backup_path(directory, database)

    try:
        # "xb": never overwrite an existing backup; a name clash is a clean error.
        with target.open("xb") as handle:
            result = compose_exec(
                "pg_dump", "-U", user, "-d", database, "-Fc", "--no-owner", "--no-privileges",
                stdout=handle,
            )  # fmt: skip
        if result.returncode != 0:
            raise Failed(f"pg_dump failed: {_error(result)}")
        if target.stat().st_size == 0:
            raise Failed("pg_dump produced an empty file")

        with target.open("rb") as handle:
            listing = compose_exec("pg_restore", "--list", stdin=handle)
        if listing.returncode != 0:
            raise Failed(f"the new backup is not a readable archive: {_error(listing)}")
    except FileExistsError:
        raise Refused(f"{target} already exists; refusing to overwrite a backup") from None
    except BaseException:
        # Leave nothing half-written behind (the file was created by this call: "xb" succeeded).
        target.unlink(missing_ok=True)
        raise

    with_posix_perms(lambda: os.chmod(target, 0o600))
    checksum = sha256_of(target)
    sidecar = target.with_name(target.name + ".sha256")
    sidecar.write_text(f"{checksum}  {target.name}\n", encoding="utf-8")
    with_posix_perms(lambda: os.chmod(sidecar, 0o600))
    print(f"backup written: {target}")
    print(f"  size:   {target.stat().st_size:,} bytes")
    print(f"  sha256: {checksum}")
    return target


def with_posix_perms(action) -> None:
    """chmod is best-effort: Windows ignores POSIX modes (the folder's ACL applies instead)."""
    try:
        action()
    except (OSError, NotImplementedError):
        pass


# --------------------------------------------------------------------------------------------
# verify (disposable container)
# --------------------------------------------------------------------------------------------


def verify(file: Path, image: str = VERIFY_IMAGE) -> dict[str, int]:
    if not file.is_file():
        raise Refused(f"backup file not found: {file}")
    check_sidecar(file)

    name = f"cloudpulse-verify-{secrets.token_hex(4)}"
    # A random one-off password, passed by variable name so it never appears in a command line.
    env = {**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(24)}
    started = run(
        ["docker", "run", "--detach", "--rm", "--name", name, "--tmpfs",
         "/var/lib/postgresql/data", "--env", "POSTGRES_PASSWORD", image],
        env=env,
    )  # fmt: skip
    if started.returncode != 0:
        raise Failed(f"could not start the disposable verification container: {_error(started)}")
    try:
        _wait_until_ready(name)
        created = run(["docker", "exec", name, "createdb", "-U", "postgres", "restore_check"])
        if created.returncode != 0:
            raise Failed(f"createdb failed: {_error(created)}")
        with file.open("rb") as handle:
            restored = run(
                ["docker", "exec", "-i", name, "pg_restore", "-U", "postgres", "-d",
                 "restore_check", "--no-owner", "--exit-on-error"],
                stdin=handle,
            )  # fmt: skip
        if restored.returncode != 0:
            raise Failed(f"restore into the disposable database failed: {_error(restored)}")

        counts = {}
        for table in EXPECTED_TABLES:
            result = run(
                ["docker", "exec", name, "psql", "-U", "postgres", "-d", "restore_check", "-tAc",
                 f"select count(*) from public.{table}"],
            )  # fmt: skip
            if result.returncode != 0:
                raise _BadBackup(f"expected table '{table}' is missing from the backup")
            counts[table] = int(_text(result))
        if counts["alembic_version"] != 1:
            raise _BadBackup("the backup does not record exactly one schema version")
    finally:
        run(["docker", "rm", "--force", name])

    print(f"verified {file.name} by restoring it into a disposable database:")
    for table, count in counts.items():
        print(f"  {table:<24} {count:>10,} rows")
    print("the disposable container has been removed; the live database was not touched")
    return counts


class _BadBackup(Failed):
    """The archive restored but does not look like a CloudPulse database (exit code 3)."""


def _wait_until_ready(container: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = run(["docker", "exec", container, "pg_isready", "-U", "postgres"])
        if ready.returncode == 0:
            # The image restarts once after initdb; require two consecutive successes.
            time.sleep(1.5)
            if run(["docker", "exec", container, "pg_isready", "-U", "postgres"]).returncode == 0:
                return
        time.sleep(1)
    raise Failed("the disposable database did not become ready in time")


# --------------------------------------------------------------------------------------------
# restore (into an EMPTY database only)
# --------------------------------------------------------------------------------------------


def restore(file: Path, database: str, allow_live_name: bool = False) -> None:
    if not file.is_file():
        raise Refused(f"backup file not found: {file}")
    validate_database_name(database)
    check_sidecar(file)
    user, live = db_settings()

    if database == live and not allow_live_name:
        raise Refused(
            f"'{database}' is the live database. Restore into a new name, or, on a freshly created "
            "stack where it is still empty, pass --allow-live-database-name."
        )

    exists = psql(user, "postgres", f"select 1 from pg_database where datname = '{database}'")
    if exists:
        tables = int(
            psql(
                user, database,
                "select count(*) from information_schema.tables "
                "where table_schema not in ('pg_catalog', 'information_schema')",
            )
        )  # fmt: skip
        if tables > 0:
            raise Refused(
                f"database '{database}' already contains {tables} tables; refusing to restore "
                "over existing data. Choose a new database name."
            )
    else:
        psql(user, "postgres", f'create database "{database}"')
        print(f"created empty database '{database}'")

    with file.open("rb") as handle:
        result = compose_exec(
            "pg_restore", "-U", user, "-d", database, "--no-owner", "--exit-on-error", stdin=handle
        )
    if result.returncode != 0:
        raise Failed(
            f"restore failed: {_error(result)}\n"
            f"The database '{database}' may be partially populated; drop it and retry."
        )
    rows = psql(user, database, "select count(*) from public.monitored_resources")
    print(f"restored {file.name} into '{database}' ({rows} monitored resources)")


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backup", help="write a new backup file")
    b.add_argument("--output-dir", type=Path, default=DEFAULT_BACKUP_DIR)

    v = sub.add_parser("verify", help="restore a backup into a disposable container and check it")
    v.add_argument("file", type=Path)
    v.add_argument("--image", default=VERIFY_IMAGE, help="PostgreSQL image for the disposable DB")

    r = sub.add_parser("restore", help="restore into a new/empty database on the db service")
    r.add_argument("file", type=Path)
    r.add_argument("--database", required=True, help="target database name (must be new or empty)")
    r.add_argument("--allow-live-database-name", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            backup(args.output_dir)
        elif args.command == "verify":
            verify(args.file, args.image)
        else:
            restore(args.file, args.database, args.allow_live_database_name)
    except _BadBackup as error:
        print(f"BACKUP PROBLEM: {error}", file=sys.stderr)
        return EXIT_BAD_BACKUP
    except Refused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except Failed as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
