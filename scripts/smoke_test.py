#!/usr/bin/env python3
"""End-to-end smoke test of a running CloudPulse stack (standard library only).

Requires the stack to be up with the demo target reachable by the worker:

    MONITORING_ALLOWED_LOCAL_HOSTS=demo-target:8080 MONITORING_MIN_INTERVAL_SECONDS=5 \
        docker compose --profile demo up --build --wait
    python scripts/smoke_test.py --restart-worker

It registers resources against the demo nginx and verifies, end to end: scheduled checks
run and are recorded as history, manual checks enter history too, summary and time-series
metrics agree with the recorded rows, history survives a worker restart, disabling stops new
measurements (but keeps history), and unsafe URLs are rejected. With --docker-control it also
runs the alerting scenario: a consecutive-failure rule fires exactly one incident when the demo
target is stopped, the incident is acknowledged, survives a worker restart, and resolves itself
when the target returns. It deletes what it creates.
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


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "--profile", "demo", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )


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

    if do_restart:
        alert_scenario(api, created)
    else:
        print("alerting scenario skipped (needs --docker-control to stop/start the demo target)")


# --- Alerting scenario ----------------------------------------------------------------------

RULE_FAILURES = 3
RULE_RECOVERY = 2


def resource_incidents(api: Api, resource_id: str) -> list[dict]:
    status, body = api.call("GET", f"/api/v1/incidents?resource_id={resource_id}&limit=200")
    assert status == 200, body
    return body["items"]


def get_rule(api: Api, rule_id: str) -> dict:
    status, body = api.call("GET", f"/api/v1/alert-rules/{rule_id}")
    assert status == 200, body
    return body


def alert_scenario(api: Api, created: list[str]) -> None:
    print("alerting: rule, incident lifecycle, restart, recovery")
    target_stopped = False
    try:
        status, resource = api.call(
            "POST",
            "/api/v1/resources",
            {"name": "smoke-alerting", "url": f"{TARGET}/", "check_interval_seconds": INTERVAL},
        )
        expect(status == 201, "alerting resource registered")
        rid = resource["id"]
        created.append(rid)

        status, rule = api.call(
            "POST",
            f"/api/v1/resources/{rid}/alert-rules",
            {
                "name": "3 consecutive failures",
                "rule_type": "CONSECUTIVE_FAILURES",
                "consecutive_failures": RULE_FAILURES,
                "recovery_successes": RULE_RECOVERY,
                "severity": "CRITICAL",
            },
        )
        expect(status == 201, "consecutive-failure rule created")
        rule_id = rule["id"]
        expect(
            (rule["current_state"], rule["failure_streak"]) == ("INSUFFICIENT_DATA", 0),
            "a new rule starts as INSUFFICIENT_DATA with zero counters",
        )

        wait_for(
            "rule evaluates healthy checks",
            lambda: get_rule(api, rule_id)["current_state"] == "OK",
            timeout=INTERVAL * 6 + 20,
        )
        first_eval = get_rule(api, rule_id)["last_evaluated_at"]
        wait_for(
            "a second evaluation",
            lambda: get_rule(api, rule_id)["last_evaluated_at"] != first_eval,
            timeout=INTERVAL * 6 + 20,
        )
        expect(resource_incidents(api, rid) == [], "healthy checks open no incident")
        expect(get_rule(api, rule_id)["failure_streak"] == 0, "healthy checks keep the streak at 0")

        print("  ... stopping the demo target")
        compose("stop", "demo-target")
        target_stopped = True

        seen = wait_for(
            "failed checks advance the rule counter",
            lambda: (lambda r: r if r["failure_streak"] >= 1 else None)(get_rule(api, rule_id)),
            timeout=INTERVAL * 6 + 20,
        )
        expect(seen["failure_streak"] >= 1, "a failed check advanced the failure counter")
        if seen["failure_streak"] < RULE_FAILURES:
            expect(
                seen["current_state"] == "OK" and resource_incidents(api, rid) == [],
                f"below {RULE_FAILURES} failures the rule is not firing and no incident exists",
            )

        firing = wait_for(
            "rule reaches FIRING",
            lambda: (lambda r: r if r["current_state"] == "FIRING" else None)(
                get_rule(api, rule_id)
            ),
            timeout=INTERVAL * 8 + 30,
        )
        expect(firing["failure_streak"] >= RULE_FAILURES, "rule fired at the required threshold")
        (incident,) = resource_incidents(api, rid)
        expect(
            incident["status"] == "OPEN" and incident["severity"] == "CRITICAL",
            "exactly one OPEN CRITICAL incident was opened",
        )
        expect(incident["alert_rule_id"] == rule_id, "the incident belongs to the rule")

        streak_at_open = firing["failure_streak"]
        wait_for(
            "further unhealthy checks",
            lambda: get_rule(api, rule_id)["failure_streak"] >= streak_at_open + 2,
            timeout=INTERVAL * 8 + 30,
        )
        expect(len(resource_incidents(api, rid)) == 1, "more unhealthy checks open no duplicate")
        expect(
            get_rule(api, rule_id)["current_state"] == "FIRING", "the rule stays FIRING meanwhile"
        )

        status, acked = api.call("POST", f"/api/v1/incidents/{incident['id']}/acknowledge")
        expect(status == 200 and acked["status"] == "ACKNOWLEDGED", "incident acknowledged")
        status, again = api.call("POST", f"/api/v1/incidents/{incident['id']}/acknowledge")
        expect(
            status == 200 and again["acknowledged_at"] == acked["acknowledged_at"],
            "acknowledging again is a harmless no-op",
        )
        expect(get_rule(api, rule_id)["current_state"] == "FIRING", "acknowledging did not resolve")

        print("  ... restarting the worker while the incident is active")
        before_restart = get_rule(api, rule_id)["failure_streak"]
        restart_worker()
        after = get_rule(api, rule_id)
        expect(
            after["current_state"] == "FIRING" and after["failure_streak"] >= before_restart,
            "rule progress persisted across the worker restart",
        )
        expect(
            [i["status"] for i in resource_incidents(api, rid)] == ["ACKNOWLEDGED"],
            "the acknowledged incident persisted across the restart",
        )
        wait_for(
            "checks resume after the restart",
            lambda: get_rule(api, rule_id)["failure_streak"] > before_restart,
            timeout=INTERVAL * 8 + 30,
        )
        expect(len(resource_incidents(api, rid)) == 1, "still exactly one incident after restart")

        print("  ... restoring the demo target")
        compose("start", "demo-target")
        target_stopped = False

        def resolved():
            (current,) = resource_incidents(api, rid)
            return current if current["status"] == "RESOLVED" else None

        done = wait_for("recovery resolves the incident", resolved, timeout=INTERVAL * 12 + 60)
        expect(done["resolution_reason"] == "RECOVERED", "resolved automatically as RECOVERED")
        expect(
            done["acknowledged_at"] is not None,
            "the acknowledgement is kept on the resolved incident",
        )
        final_rule = get_rule(api, rule_id)
        expect(
            (final_rule["current_state"], final_rule["failure_streak"], final_rule["recovery_streak"])
            == ("OK", 0, 0),
            "the rule returned to OK with counters reset",
        )

        status, timeline = api.call("GET", f"/api/v1/incidents/{done['id']}/events")
        expect(status == 200, "incident timeline available")
        kinds = [(e["event_type"], e["actor"]) for e in timeline["items"]]
        expect(
            kinds == [("OPENED", "SYSTEM"), ("ACKNOWLEDGED", "MANUAL"), ("RECOVERED", "SYSTEM")],
            "timeline is OPENED -> ACKNOWLEDGED -> RECOVERED",
        )
        times = [e["occurred_at"] for e in timeline["items"]]
        expect(times == sorted(times), "timeline timestamps are in order")

        history_rows = api.history(rid, limit=200)["items"]
        by_id = {row["id"]: row for row in history_rows}
        opener = by_id.get(done["opened_by_check_id"])
        expect(
            opener is not None
            and opener["status"] == "DOWN"
            and opener["checked_at"] == done["triggered_at"],
            "the incident points at the exact DOWN measurement that opened it",
        )
        expect(
            sum(1 for r in history_rows if r["status"] == "DOWN") >= RULE_FAILURES
            and history_rows[0]["status"] == "HEALTHY",
            "history shows the outage and the recovery",
        )

        def stable_snapshot():
            before = api.status_of(rid)["last_checked_at"]
            newest = api.history(rid, limit=1)["items"][0]["checked_at"]
            return (before, newest) if before == api.status_of(rid)["last_checked_at"] else None

        last, newest = wait_for("a stable snapshot", stable_snapshot, timeout=30)
        expect(last == newest, "latest state and newest history row agree")
        expect(api.status_of(rid)["current_status"] == "HEALTHY", "the resource is HEALTHY again")

        print("  ... deleting the resource: incident history must remain")
        status, _ = api.call("DELETE", f"/api/v1/resources/{rid}")
        expect(status == 204, "resource deleted")
        created.remove(rid)
        status, kept = api.call("GET", f"/api/v1/incidents/{done['id']}")
        expect(
            status == 200
            and kept["resource_id"] is None
            and kept["resource_name"] == "smoke-alerting"
            and kept["resolution_reason"] == "RECOVERED",
            "the incident survives resource deletion with its name snapshot",
        )
        expect(
            api.call("GET", f"/api/v1/alert-rules/{rule_id}")[0] == 404,
            "the rule was removed with its resource",
        )
    finally:
        if target_stopped:
            compose("start", "demo-target")


def main() -> int:
    parser = argparse.ArgumentParser(description="CloudPulse end-to-end smoke test")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument(
        "--docker-control",
        "--restart-worker",  # earlier name, kept as an alias
        dest="docker_control",
        action="store_true",
        help="let the test restart the worker and stop/start the demo target (needs docker)",
    )
    args = parser.parse_args()

    api = Api(args.api)
    created: list[str] = []
    try:
        run(api, created, args.docker_control)
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
