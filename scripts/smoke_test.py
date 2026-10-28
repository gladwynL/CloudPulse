#!/usr/bin/env python3
"""End-to-end smoke test of a running CloudPulse stack (standard library only).

Requires the stack to be up with the demo target reachable by the worker:

    MONITORING_ALLOWED_LOCAL_HOSTS=demo-target:8080 MONITORING_MIN_INTERVAL_SECONDS=5 \
        docker compose --profile demo up --build --wait
    python scripts/smoke_test.py

It registers resources against the demo nginx, then checks that scheduled checks run
automatically, a manual check works, DEGRADED/HEALTHY semantics hold, disabling stops
polling, and unsafe URLs are rejected. It deletes the resources it creates.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TARGET = "http://demo-target:8080"
INTERVAL = 5


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw else None)

    def status_of(self, resource_id: str) -> dict:
        return self.call("GET", f"/api/v1/resources/{resource_id}/status")[1]


def wait_for(description: str, producer, timeout: float = 60.0, interval: float = 1.0):
    """Poll ``producer`` until it returns something truthy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = producer()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for: {description}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def run(api: Api, created: list[str]) -> None:
    print("readiness")
    status, body = api.call("GET", "/ready")
    expect(status == 200 and body["checks"]["database"] == "ok", "/ready reports database ok")

    print("registration and automatic checks")
    status, healthy = api.call(
        "POST",
        "/api/v1/resources",
        {"name": "smoke-healthy", "url": f"{TARGET}/", "check_interval_seconds": INTERVAL},
    )
    expect(status == 201, "resource registered (201)")
    created.append(healthy["id"])
    expect(healthy["current_status"] == "UNKNOWN", "new resource starts UNKNOWN")

    _, degraded = api.call(
        "POST",
        "/api/v1/resources",
        {
            "name": "smoke-degraded",
            "url": f"{TARGET}/missing",
            "check_interval_seconds": INTERVAL,
        },
    )
    created.append(degraded["id"])

    def healthy_status():
        current = api.status_of(healthy["id"])
        return current if current["current_status"] == "HEALTHY" else None

    first = wait_for("scheduled check marks the resource HEALTHY", healthy_status)
    expect(first["last_http_status_code"] == 200, "scheduled check recorded HTTP 200")
    expect(first["last_response_time_ms"] is not None, "response time recorded")
    expect(first["last_error"] is None, "no error recorded for a healthy resource")

    def degraded_status():
        current = api.status_of(degraded["id"])
        return current if current["current_status"] == "DEGRADED" else None

    bad = wait_for("an unexpected status marks the resource DEGRADED", degraded_status)
    expect(bad["last_http_status_code"] == 404, "HTTP 404 recorded as DEGRADED (not DOWN)")

    print("re-checks happen on schedule")

    def rechecked():
        current = api.status_of(healthy["id"])
        return current if current["last_checked_at"] != first["last_checked_at"] else None

    second = wait_for("a second scheduled check", rechecked, timeout=INTERVAL * 4 + 15)
    expect(second["last_checked_at"] > first["last_checked_at"], "last_checked_at advanced")

    print("manual check")
    status, manual = api.call("POST", f"/api/v1/resources/{healthy['id']}/check")
    expect(status == 200 and manual["current_status"] == "HEALTHY", "manual check is HEALTHY")
    expect(manual["last_checked_at"] >= second["last_checked_at"], "manual check was recorded")

    print("disabling stops polling")
    status, disabled = api.call("PATCH", f"/api/v1/resources/{healthy['id']}", {"is_enabled": False})
    expect(status == 200 and disabled["next_check_at"] is None, "resource disabled and unscheduled")
    expect(disabled["current_status"] == "HEALTHY", "last observation retained while disabled")
    stamp = api.status_of(healthy["id"])["last_checked_at"]
    time.sleep(INTERVAL * 2 + 3)
    expect(api.status_of(healthy["id"])["last_checked_at"] == stamp, "no checks ran while disabled")
    status, _ = api.call("POST", f"/api/v1/resources/{healthy['id']}/check")
    expect(status == 409, "manual check of a disabled resource is refused (409)")

    print("network safety")
    for url in (
        "http://127.0.0.1:8000/health",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://user:pw@example.com/",
        "ftp://example.com/",
    ):
        status, _ = api.call("POST", "/api/v1/resources", {"name": "bad", "url": url})
        expect(status == 422, f"registration of {url} rejected (422)")


def main() -> int:
    parser = argparse.ArgumentParser(description="CloudPulse end-to-end smoke test")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()

    api = Api(args.api)
    created: list[str] = []
    try:
        run(api, created)
    except AssertionError as error:
        print(f"\nSMOKE TEST FAILED: {error}", file=sys.stderr)
        return 1
    finally:
        for resource_id in created:
            api.call("DELETE", f"/api/v1/resources/{resource_id}")
    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
