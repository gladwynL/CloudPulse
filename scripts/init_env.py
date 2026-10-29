#!/usr/bin/env python3
"""Create a local `.env` from `.env.example` with a freshly generated database password.

    python scripts/init_env.py            # writes .env (refuses to overwrite an existing one)
    python scripts/init_env.py --check    # validates an existing .env without printing secrets

The password is random (24 bytes, URL-safe), written only to the new file, and never printed.
Existing files are never overwritten: remove or rename `.env` yourself if you really want a new one
(note that changing the password of an existing database volume requires more than editing .env).
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEAK = {"", "change-me", "changeme", "password", "postgres", "cloudpulse", "admin", "secret"}
PASSWORD_LINE = re.compile(r"^POSTGRES_PASSWORD=.*$", re.MULTILINE)


def render(template: str, password: str) -> str:
    """Return the template with POSTGRES_PASSWORD set; raises if the template lacks the line."""
    if not PASSWORD_LINE.search(template):
        raise ValueError("the template has no POSTGRES_PASSWORD line")
    return PASSWORD_LINE.sub(lambda _m: f"POSTGRES_PASSWORD={password}", template, count=1)


def password_problem(env_text: str) -> str | None:
    match = PASSWORD_LINE.search(env_text)
    if not match:
        return "POSTGRES_PASSWORD is not set"
    value = match.group(0).split("=", 1)[1].strip()
    if value.lower() in WEAK:
        return "POSTGRES_PASSWORD is empty or a placeholder"
    if len(value) < 12:
        return "POSTGRES_PASSWORD is shorter than 12 characters"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--check", action="store_true", help="validate an existing env file")
    parser.add_argument("--template", type=Path, default=REPO_ROOT / ".env.example")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / ".env")
    args = parser.parse_args(argv)

    if args.check:
        if not args.output.is_file():
            print(f"{args.output} does not exist", file=sys.stderr)
            return 1
        problem = password_problem(args.output.read_text(encoding="utf-8"))
        if problem:
            print(f"PROBLEM: {problem}", file=sys.stderr)
            return 1
        print("env file looks fine (no secrets shown)")
        return 0

    if args.output.exists():
        print(f"REFUSED: {args.output} already exists; not overwriting it", file=sys.stderr)
        return 2
    content = render(args.template.read_text(encoding="utf-8"), secrets.token_urlsafe(24))
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError:
        print(f"REFUSED: {args.output} already exists; not overwriting it", file=sys.stderr)
        return 2
    try:
        os.chmod(args.output, 0o600)
    except OSError:
        pass  # Windows: the profile folder's ACL already restricts access
    print(f"wrote {args.output} with a generated POSTGRES_PASSWORD (not displayed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
