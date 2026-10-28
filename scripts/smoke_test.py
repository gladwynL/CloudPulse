#!/usr/bin/env python3
"""End-to-end smoke test of a running CloudPulse stack (standard library only).

Requires the stack to be up with the demo target reachable by the worker:

    MONITORING_ALLOWED_LOCAL_HOSTS=demo-target:8080 MONITORING_MIN_INTERVAL_SECONDS=5 \
        docker compose --profile demo up --build --wait
    python scripts/smoke_test.py --restart-worker

It registers resources against the demo nginx and verifies, end to end: scheduled checks
run and are recorded as history, manual checks enter history too, summary and time-series
metrics agree with the recorded rows, history survives a worker restart, disabling stops new
measurements (but keeps history), and unsafe URLs are rejected. It deletes what it creates.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TARGET = "http://demo-target:8080"
INTERVAL = 5
REPO_ROOT = Path(__file__).resolve().parent.parent


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

    def history(self, resource_id: str, **params: object) -> dict:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"/api/v1/resources/{resource_id}/checks" + (f"?{query}" if query else "")
        status, body = self.call("GET", path)
        assert status == 200, f"history returned {status}: {body}"
        return body

    def metrics(self, resource_id: str, kind: str, **params: object) -> dict:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"/api/v1/resources/{resource_id}/metrics/{kind}" + (f"?{query}" if query else "")
        status, body = self.call("GET", path)
        assert status == 200, f"{kind} returned {status}: {body}"
        return body

    def count(self, resource_id: str) -> int:
        return len(self.history(resource_id, limit=200)["items"])


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


def restart_worker() -> None:
    subprocess.run(
        ["docker", "compose", "--profile", "demo", "restart", "worker"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )


def run(api: Api, created: list[str], do_restart: bool) -> None:
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
    rid = healthy["id"]
    created.append(rid)
    expect(healthy["current_status"] == "UNKNOWN", "new resource starts UNKNOWN")

    # A disabled resource is never scheduled, so "never checked" is deterministic.
    _, never = api.call(
        "POST",
        "/api/v1/resources",
        {"name": "smoke-never-checked", "url": f"{TARGET}/", "is_enabled": False},
    )
    created.append(never["id"])
    expect(api.history(never["id"])["items"] == [], "a never-checked resource has an empty history")
    empty = api.metrics(never["id"], "summary")
    expect(
        empty["checks"]["total"] == 0 and empty["availability_percent"] is None,
        "empty history gives null availability (not 0 or 100)",
    )
    expect(empty["latency"]["avg_ms"] is None, "empty history gives null latency aggregates")
    expect(api.metrics(never["id"], "timeseries")["buckets"] == [], "and no time-series buckets")

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

    def has_status(resource_id: str, wanted: str):
        current = api.status_of(resource_id)
        return current if current["current_status"] == wanted else None

    first = wait_for("scheduled check marks resource HEALTHY", lambda: has_status(rid, "HEALTHY"))
    expect(first["last_http_status_code"] == 200, "scheduled check recorded HTTP 200")
    bad = wait_for(
        "unexpected status marks resource DEGRADED",
        lambda: has_status(degraded["id"], "DEGRADED"),
    )
    expect(bad["last_http_status_code"] == 404, "HTTP 404 recorded as DEGRADED (not DOWN)")

    print("history is persisted and grows")
    wait_for("two scheduled measurements", lambda: api.count(rid) >= 2, timeout=INTERVAL * 6 + 15)
    items = api.history(rid)["items"]
    expect(all(i["source"] == "SCHEDULED" for i in items), "scheduled checks are recorded as SCHEDULED")
    expect(all(i["status"] == "HEALTHY" for i in items), "history rows carry the check status")
    stamps = [i["checked_at"] for i in items]
    expect(stamps == sorted(stamps, reverse=True), "history is newest first")


    def consistent_snapshot():
        # A scheduled check could land between the two reads; only compare a stable pair.
        before = api.status_of(rid)["last_checked_at"]
        newest_row = api.history(rid, limit=1)["items"][0]["checked_at"]
        after = api.status_of(rid)["last_checked_at"]
        return (before, newest_row) if before == after else None

    last_checked, newest_checked = wait_for("a stable snapshot", consistent_snapshot, timeout=30)
    expect(
        newest_checked == last_checked,
        "newest history row has the same timestamp as the resource's last check (one transaction)",
    )
    expect(
        all(i["response_time_ms"] is not None and i["duration_ms"] >= 0 for i in items),
        "latency and execution duration are recorded",
    )
    bad_rows = wait_for(
        "degraded history row",
        lambda: [i for i in api.history(degraded["id"])["items"] if i["status"] == "DEGRADED"],
    )
    expect(bad_rows[0]["http_status_code"] == 404, "degraded row keeps the HTTP status")
    expect(
        api.metrics(degraded["id"], "summary")["availability_percent"] == 0.0,
        "an always-degraded resource has 0.0% (not null) availability",
    )

    print("manual checks enter history")
    before = api.count(rid)
    status, manual = api.call("POST", f"/api/v1/resources/{rid}/check")
    expect(status == 200 and manual["current_status"] == "HEALTHY", "manual check is HEALTHY")
    newest = api.history(rid, limit=1)["items"][0]
    expect(newest["source"] == "MANUAL", "the manual check is the newest row, source MANUAL")
    expect(api.count(rid) >= before + 1, "history grew by the manual check")

    print("pagination")
    page = api.history(rid, limit=1)
    expect(len(page["items"]) == 1 and page["next_cursor"], "limit=1 returns a cursor")
    second = api.history(rid, limit=1, cursor=page["next_cursor"])
    expect(second["items"][0]["id"] != page["items"][0]["id"], "the cursor yields the next row")

    if do_restart:
        print("worker restart")
        count_before = api.count(rid)
        restart_worker()
        expect(api.count(rid) >= count_before, "history survives a worker restart")
        wait_for(
            "checks resume after restart",
            lambda: api.count(rid) > count_before,
            timeout=INTERVAL * 8 + 30,
        )
        print("  ok  new measurements are recorded after the restart")

    print("disabling stops new measurements but keeps history")
    status, disabled = api.call("PATCH", f"/api/v1/resources/{rid}", {"is_enabled": False})
    expect(status == 200 and disabled["next_check_at"] is None, "resource disabled")
    time.sleep(2)  # let any in-flight check finish; its result is discarded or already stored
    settled = api.count(rid)
    time.sleep(INTERVAL * 2 + 3)
    expect(api.count(rid) == settled, "no new measurements while disabled")
    expect(settled >= 3, "history is retained while disabled")
    status, _ = api.call("POST", f"/api/v1/resources/{rid}/check")
    expect(status == 409, "manual check of a disabled resource is refused (409)")

    print("metrics agree with the recorded rows")
    rows = api.history(rid, limit=200)["items"]
    summary = api.metrics(rid, "summary")
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("HEALTHY", "DEGRADED", "DOWN")}
    expect(summary["checks"]["total"] == len(rows), f"summary total matches history ({len(rows)})")
    expect(
        (summary["checks"]["healthy"], summary["checks"]["degraded"], summary["checks"]["down"])
        == (counts["HEALTHY"], counts["DEGRADED"], counts["DOWN"]),
        "summary status counts match history",
    )
    expect(
        summary["availability_percent"] == round(100 * counts["HEALTHY"] / len(rows), 2),
        "availability = healthy / total * 100",
    )
    latencies = [r["response_time_ms"] for r in rows if r["response_time_ms"] is not None]
    lat = summary["latency"]
    expect(lat["samples"] == len(latencies), "latency sample count matches")
    expect(abs(lat["avg_ms"] - statistics.fmean(latencies)) < 0.02, "average latency matches")
    expect(lat["min_ms"] == min(latencies) and lat["max_ms"] == max(latencies), "min/max match")
    expect(lat["min_ms"] <= lat["p50_ms"] <= lat["p95_ms"] <= lat["max_ms"], "percentiles ordered")

    series = api.metrics(rid, "timeseries")
    buckets = series["buckets"]
    expect(len(buckets) >= 1, f"time series returned buckets (interval {series['interval']})")
    expect(
        [b["bucket_start"] for b in buckets] == sorted(b["bucket_start"] for b in buckets),
        "buckets are in time order",
    )
    expect(
        sum(b["total"] for b in buckets) == summary["checks"]["total"],
        "bucket totals add up to the summary total",
    )
    expect(
        all(b["healthy"] + b["degraded"] + b["down"] == b["total"] for b in buckets),
        "each bucket's counts add up",
    )

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

    print("history is removed with its resource")
    status, _ = api.call("DELETE", f"/api/v1/resources/{rid}")
    expect(status == 204, "resource deleted")
    created.remove(rid)
    expect(api.call("GET", f"/api/v1/resources/{rid}/checks")[0] == 404, "its history is gone (404)")


def main() -> int:
    parser = argparse.ArgumentParser(description="CloudPulse end-to-end smoke test")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument(
        "--restart-worker",
        action="store_true",
        help="restart the worker container mid-test (needs the docker CLI)",
    )
    args = parser.parse_args()

    api = Api(args.api)
    created: list[str] = []
    try:
        run(api, created, args.restart_worker)
    except (AssertionError, subprocess.CalledProcessError) as error:
        print(f"\nSMOKE TEST FAILED: {error}", file=sys.stderr)
        return 1
    finally:
        for resource_id in created:
            api.call("DELETE", f"/api/v1/resources/{resource_id}")
    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
