#!/usr/bin/env python3
"""Audit which container ports a running Compose stack publishes on the host.

    python scripts/check_ports.py                       # every published port must be loopback-only
    python scripts/check_ports.py --only frontend       # ...and only the frontend may publish any

Exit 0 when the rules hold, 1 when a port is published on a non-loopback address (or by a service
that is not allowed to publish), 2 when the stack cannot be inspected. Honours the usual
COMPOSE_FILE / COMPOSE_PROJECT_NAME variables.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def audit(
    published: dict[str, list[dict[str, object]]], only: set[str] | None = None
) -> list[str]:
    """Return a list of violations for ``{service: [publisher, ...]}`` (empty means compliant)."""
    problems = []
    for service, publishers in sorted(published.items()):
        for publisher in publishers:
            port = publisher.get("PublishedPort") or 0
            if not port:
                continue  # exposed inside the network only
            address = str(publisher.get("URL") or "0.0.0.0")
            if address not in LOOPBACK:
                problems.append(f"{service}: port {port} is published on {address}, not loopback")
            if only is not None and service not in only:
                problems.append(f"{service}: publishes port {port} but only {sorted(only)} may")
    return problems


def inspect_stack() -> dict[str, list[dict[str, object]]]:
    result = subprocess.run(  # noqa: S603
        ["docker", "compose", "--profile", "demo", "ps", "--all", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "docker compose ps failed")
    published: dict[str, list[dict[str, object]]] = {}
    for line in result.stdout.splitlines():
        if line.strip():
            row = json.loads(line)
            published[row["Service"]] = row.get("Publishers") or []
    return published


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--only", action="append", help="the only service(s) allowed to publish")
    args = parser.parse_args(argv)
    try:
        published = inspect_stack()
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"cannot inspect the stack: {error}", file=sys.stderr)
        return 2
    if not published:
        print("no containers found; is the stack running?", file=sys.stderr)
        return 2
    for service, publishers in sorted(published.items()):
        shown = [
            f"{p.get('URL')}:{p.get('PublishedPort')}->{p.get('TargetPort')}"
            for p in publishers
            if p.get("PublishedPort")
        ]
        print(f"  {service:<12} {', '.join(shown) if shown else '(no host ports)'}")
    problems = audit(published, set(args.only) if args.only else None)
    for problem in problems:
        print(f"VIOLATION: {problem}", file=sys.stderr)
    if not problems:
        print("port audit passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
