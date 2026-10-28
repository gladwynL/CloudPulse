# CloudPulse

[![CI](https://github.com/gladwynL/CloudPulse/actions/workflows/ci.yml/badge.svg)](https://github.com/gladwynL/CloudPulse/actions/workflows/ci.yml)

**CloudPulse** is a cloud monitoring and observability platform being built as a
production-style engineering project: health checks, uptime and latency tracking,
alerting, and incident management for cloud services, with a React dashboard on top.

> **Current status: Phase 2 — Metrics ingestion and historical storage.**
> You can register HTTP services, have a worker check them on a schedule, trigger manual
> checks, and read each resource's latest health. **Every accepted check is also stored as a
> durable history row**, queryable through REST APIs for check history, availability/latency
> summaries, and time-bucketed series (the data a future dashboard will chart). Old history is
> pruned by a retention policy.
>
> **Not implemented yet:** alert rules, incidents, notifications, authentication, the
> monitoring dashboard (charts and resource management UI), and cloud deployment.
> See the [roadmap](#roadmap).

---

## Contents

- [Portfolio context](#portfolio-context)
- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Quick start (Docker Compose)](#quick-start-docker-compose)
- [Monitoring guide](#monitoring-guide)
- [History and metrics](#history-and-metrics)
- [Scheduling and worker design](#scheduling-and-worker-design)
- [Network safety (SSRF)](#network-safety-ssrf)
- [Security and deployment limitations](#security-and-deployment-limitations)
- [Environment configuration](#environment-configuration)
- [Local development without Docker](#local-development-without-docker)
- [Database migrations](#database-migrations)
- [Testing and linting](#testing-and-linting)
- [Health and readiness endpoints](#health-and-readiness-endpoints)
- [Continuous integration](#continuous-integration)
- [Portability notes](#portability-notes)
- [Roadmap](#roadmap)

## Portfolio context

CloudPulse is project #3 in a software-engineering portfolio:

1. **DevTrack** — production-style full-stack project/issue tracker
2. **RepoPilot** — AI-powered GitHub pull request reviewer
3. **CloudPulse** — cloud monitoring and observability platform (this repository)

It focuses on cloud/backend engineering: observability, monitoring architecture,
PostgreSQL, Docker, CI/CD, production API design, configuration management, and
Azure-oriented deployment.

## Capabilities

| Capability | Status |
| --- | --- |
| Register monitored HTTP resources (URL, interval, timeout, expected status) | ✅ Phase 1 |
| Enable / disable monitoring per resource | ✅ Phase 1 |
| Automatic periodic HTTP checks by a dedicated worker | ✅ Phase 1 |
| Latest health state, response time, status code, and error per resource | ✅ Phase 1 |
| Durable history of every accepted check (atomic with the latest state) | ✅ Phase 2 |
| Check history API (filters, keyset pagination) | ✅ Phase 2 |
| Availability and latency summary (avg/min/max/p50/p95) | ✅ Phase 2 |
| Time-bucketed series API (1m–1d buckets) for future charts | ✅ Phase 2 |
| Retention cleanup of old history (default 30 days) | ✅ Phase 2 |
| Manual on-demand checks | ✅ Phase 1 |
| SSRF protections for user-supplied URLs | ✅ Phase 1 |
| Structured JSON logging, health/readiness probes, Docker, CI | ✅ Phase 0 |
| Alert rules, incidents | Planned (Phase 3) |
| Full React monitoring dashboard | Planned (Phase 4) |
| Azure deployment, infrastructure-as-code | Planned (Phase 5) |

## Architecture

```
                         ┌────────────────────────────┐
Browser ─► frontend ───► │ backend (FastAPI)          │ ──┐
 (React shell, nginx)    │  /health /ready            │   │
                         │  /api/v1/resources...      │   ▼
curl / Swagger ────────► │  manual checks (HTTP out)  │  PostgreSQL ◄──┐
                         └────────────────────────────┘  (resources +  │
                                                          leases)      │
 migrate (one-shot: alembic upgrade head) ─────────────────►           │
                                                                       │
                         ┌────────────────────────────┐                │
                         │ worker (python -m app.worker) ──────────────┘
                         │  polls for due resources,  │
                         │  runs checks (HTTP out)    │ ──► monitored services
                         └────────────────────────────┘
```

- **backend** — FastAPI app (`create_app()` factory). Serves the resource API and probes.
  It never polls; it only performs a check when asked to (manual check).
- **worker** — the single polling process. Same Docker image as the backend, different
  command. Claims due resources from PostgreSQL, checks them with bounded concurrency,
  and stores each result (latest state + history row, atomically). It also runs the
  retention cleanup loop.
- **db** — PostgreSQL 17. It is the source of truth for configuration, latest results,
  scheduling state (`next_check_at` plus a per-resource lease), **and check history**
  (`resource_check_results`).
- **migrate** — one-shot job that applies Alembic migrations before backend and worker start.
- **frontend** — React shell with the API status panel. Not a monitoring dashboard yet.

Layering inside `backend/app/`: `api` (HTTP) → `services` (rules) → `repositories` (SQL),
with `monitoring` (checker, SSRF policy, executor, scheduler) shared by the API and worker.
Design notes: [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Area           | Technology                                                                 |
| -------------- | -------------------------------------------------------------------------- |
| Backend        | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, psycopg 3, Pydantic Settings, httpx |
| Database       | PostgreSQL 17                                                              |
| Frontend       | React 19, TypeScript, Vite                                                 |
| Testing        | Pytest (+ anyio) ; Vitest + Testing Library                               |
| Code quality   | Ruff (lint + format); ESLint + Prettier                                    |
| Infrastructure | Docker, Docker Compose, GitHub Actions                                     |

## Repository structure

```
CloudPulse/
├── backend/
│   ├── app/
│   │   ├── api/            # routers (health, resources), dependencies, error mapping
│   │   ├── core/           # settings, structured logging, request middleware
│   │   ├── db/             # engine/session management, declarative Base
│   │   ├── models/         # MonitoredResource, ResourceCheckResult, enums
│   │   ├── monitoring/     # checker, SSRF policy + guarded network backend,
│   │   │                   #   executor, scheduler, lease store, retention
│   │   ├── repositories/   # SQL: CRUD, lease/claim/complete, history queries, retention
│   │   ├── schemas/        # Pydantic request/response models
│   │   ├── services/       # resource rules, readiness check
│   │   ├── main.py         # create_app() factory
│   │   └── worker.py       # `python -m app.worker`
│   ├── alembic/            # migrations (0001 baseline, 0002 resources, 0003 check history)
│   ├── tests/              # unit tests + PostgreSQL integration tests
│   ├── pyproject.toml, requirements*.txt, Dockerfile
├── frontend/               # React shell (App, components, status hook, tests)
├── scripts/smoke_test.py   # end-to-end test against a running stack (stdlib only)
├── infra/                  # placeholder for Phase 5 cloud infrastructure
├── docs/architecture.md
├── .github/workflows/ci.yml
├── docker-compose.yml
├── .env.example
└── README.md
```

## Prerequisites

| Tool                                 | Version          | Needed for                          |
| ------------------------------------ | ---------------- | ----------------------------------- |
| Git                                  | any              | everything                          |
| Docker Desktop / Engine + Compose v2 | recent           | running the full stack              |
| Python                               | 3.12.x           | backend development without Docker  |
| Node.js                              | 22+ (CI uses 24) | frontend development without Docker |

Only Git and Docker are required to run the stack.

## Quick start (Docker Compose)

```bash
git clone https://github.com/gladwynL/CloudPulse.git
cd CloudPulse
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
# edit .env and set POSTGRES_PASSWORD
docker compose up --build
```

| Service    | URL                                        |
| ---------- | ------------------------------------------ |
| Frontend   | http://localhost:3000                      |
| API + Swagger UI | http://localhost:8000/docs           |
| PostgreSQL | `localhost:5432` (credentials from `.env`) |

All published ports bind to `127.0.0.1`. Startup order is enforced with health checks:
`db` (healthy) → `migrate` (completed) → `backend` and `worker` (healthy) → `frontend`.

```bash
docker compose up --build -d        # start in the background
docker compose ps                   # service status and health
docker compose logs -f worker       # follow the worker's check logs (JSON lines)
docker compose logs -f backend      # follow API logs
docker compose run --rm migrate     # re-run migrations
docker compose down                 # stop (data is kept)
docker compose down -v              # stop and DELETE the local database volume
```

### Try it with the demo target

Monitored URLs must pass the [network safety](#network-safety-ssrf) policy, which blocks
private addresses, so a local service needs an explicit development-only allowlist. The
Compose file ships a tiny nginx (`demo-target`, profile `demo`) for exactly this:

```bash
# bash/zsh:
MONITORING_ALLOWED_LOCAL_HOSTS=demo-target:8080 docker compose --profile demo up --build -d
# PowerShell:
$env:MONITORING_ALLOWED_LOCAL_HOSTS="demo-target:8080"; docker compose --profile demo up --build -d
```

Then follow the [monitoring guide](#monitoring-guide) using `http://demo-target:8080/`
(healthy) and `http://demo-target:8080/missing` (returns 404 → `DEGRADED`).
`docker compose stop demo-target` makes them `DOWN`. An automated version of this walkthrough:

```bash
MONITORING_MIN_INTERVAL_SECONDS=5 MONITORING_ALLOWED_LOCAL_HOSTS=demo-target:8080 \
  docker compose --profile demo up --build --wait
python scripts/smoke_test.py --restart-worker
```

## Monitoring guide

### Resource model

| Field | Meaning |
| --- | --- |
| `id` | UUID, server-generated |
| `name`, `description` | Human-readable label (name ≤ 100 chars) |
| `url` | `http`/`https` endpoint; checked with `GET` |
| `is_enabled` | Whether the worker schedules checks |
| `check_interval_seconds` | Time between checks (default 60; bounds 10–86400) |
| `timeout_seconds` | Hard deadline for a check (default 10; max 30; ≤ interval) |
| `expected_status_code` | HTTP status that counts as healthy (default 200) |
| `current_status` | `UNKNOWN`, `HEALTHY`, `DEGRADED`, or `DOWN` |
| `last_checked_at`, `next_check_at` | UTC timestamps |
| `last_response_time_ms` | Time until response headers arrived (null if no response) |
| `last_http_status_code`, `last_error` | Latest observation; `last_error` is a sanitized summary |

### Health states

| State | Meaning |
| --- | --- |
| `UNKNOWN` | No completed check yet, or the check configuration changed since the last one |
| `HEALTHY` | A response arrived with the expected status code |
| `DEGRADED` | A response arrived with a *different* status code (the service answered, but unhealthily). Redirects are **not** followed, so a 3xx is DEGRADED unless it is the expected status |
| `DOWN` | The check could not complete: timeout, DNS failure, connection failure, TLS failure, or a target blocked by the network policy |

`last_error` is a fixed, sanitized string such as `timeout: no response within 10s` or
`expected status 200, got 503`. Raw exception text and URLs are never stored or logged.

### API

Interactive docs: http://localhost:8000/docs

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/resources` | Create a resource (201, `Location` header) |
| `GET` | `/api/v1/resources` | List: `limit` (1–200, default 50), `offset`, `is_enabled`, `current_status`; ordered by creation time then id |
| `GET` | `/api/v1/resources/{id}` | Details |
| `PATCH` | `/api/v1/resources/{id}` | Partial update, including enable/disable |
| `DELETE` | `/api/v1/resources/{id}` | Delete (204) |
| `POST` | `/api/v1/resources/{id}/check` | Run a check now and return the resulting status |
| `GET` | `/api/v1/resources/{id}/status` | Latest health (compact view) |

Errors use FastAPI's `{"detail": ...}` envelope: `404` unknown resource, `422` invalid input
(including unsafe URLs, out-of-range intervals, malformed UUIDs), `409` for a manual check that
cannot run.

```bash
# Register a service (no outbound request happens at registration; the first check is scheduled)
curl -s -X POST localhost:8000/api/v1/resources -H 'Content-Type: application/json' \
  -d '{"name": "Billing API", "url": "https://example.com/health", "check_interval_seconds": 30}'

# Inspect the current status (use the "id" from the response)
curl -s localhost:8000/api/v1/resources/<id>/status

# Trigger a manual check
curl -s -X POST localhost:8000/api/v1/resources/<id>/check

# Disable monitoring (keeps the last observation, stops scheduling)
curl -s -X PATCH localhost:8000/api/v1/resources/<id> -H 'Content-Type: application/json' \
  -d '{"is_enabled": false}'
```

### Update, enable/disable, and manual-check semantics

- A new enabled resource is **due immediately** (status `UNKNOWN` until the first check).
- **Disabling** clears `next_check_at` and drops any in-flight lease: a check already running is
  discarded when it finishes, and nothing further is scheduled. The **last observed status and
  result are retained**. Re-enabling makes the resource due immediately.
- Changing `url`, `timeout_seconds`, or `expected_status_code` **invalidates** the previous
  observation (status → `UNKNOWN`, last-result fields cleared, in-flight check discarded) and
  schedules a fresh check. Changing only the name, description, or interval keeps the
  observation; an interval change reschedules relative to the last check.
- A **manual check** uses the same lease as scheduled checks, so it can never overlap one: it
  returns `409` if a check is already running, if the resource is disabled, or if the resource
  was changed/deleted while the check ran (the result is then discarded). It restarts the
  resource's interval timer. Its duration is bounded by the resource's timeout.

## History and metrics

Every check whose result is **accepted** (see [atomic persistence](#atomic-persistence)) appends
one row to `resource_check_results`. History is independent of the latest state: disabling or
reconfiguring a resource never alters what was recorded, and deleting a resource deletes its
history (database `ON DELETE CASCADE`, so orphans cannot exist).

### What a history row means

| Field | Meaning |
| --- | --- |
| `checked_at` | **Event time**: when the result was accepted, from the database clock. It is the transaction timestamp of the write that also updates the resource, so it equals the resource's `last_checked_at` for that check. All queries and buckets use it. |
| `created_at` | When the row was physically inserted (a few ms later). Audit only. |
| `status` | `HEALTHY`, `DEGRADED`, or `DOWN` (never `UNKNOWN`) |
| `http_status_code` | Status of the response; null when no response arrived |
| `response_time_ms` | **HTTP latency**: time until response headers arrived; null for `DOWN` |
| `duration_ms` | **Execution time** of the check (including DNS, failures and timeouts); never null. Not the same as latency. |
| `error_category`, `error_summary` | Sanitized, fixed text only (e.g. `timeout`, `timeout: no response within 10s`) |
| `source` | `SCHEDULED` or `MANUAL` |

Timestamps are timezone-aware UTC. `response_time_ms` and `duration_ms` are `NUMERIC(10,2)`;
values are rounded once, in Python, before insert, so the database never re-rounds. Database
constraints reject negative times, out-of-range HTTP codes, and `status = UNKNOWN`. No response
bodies, URLs, credentials, or raw exception text are stored.

### Atomic persistence

`repo.complete()` is the single place a check result becomes authoritative, used identically by
scheduled and manual checks (there is no separate history path). In **one transaction** it:

1. updates the resource's latest state `WHERE id = … AND lease_token = <mine>` (the lease gate);
2. if (and only if) that matched a row, inserts the history row;
3. commits.

Consequences, each covered by PostgreSQL tests:

- If the insert fails, the state update rolls back with it; the lease stays held until it expires
  and the check is retried. Nothing is half-written.
- A result discarded because the lease was lost (stale or expired lease, resource deleted,
  disabled, or reconfigured mid-check) inserts **no** history.
- Completing the same claim twice, or two workers racing to complete it, records exactly one row.
- The HTTP request runs entirely outside the transaction; no row lock is held during the call.

### Endpoints

All three require an existing resource (`404` otherwise) and share the same window rules.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/resources/{id}/checks` | History, newest first. `start`, `end`, `status`, `limit`, `cursor` |
| `GET` | `/api/v1/resources/{id}/metrics/summary` | Counts, availability, latency statistics. `start`, `end` |
| `GET` | `/api/v1/resources/{id}/metrics/timeseries` | Per-bucket aggregates. `start`, `end`, `interval` |

**Windows and timestamps.** Windows are **half-open `[start, end)`** (start inclusive, end
exclusive), so adjacent windows never double-count. Timestamps are ISO 8601; naive values are read
as UTC and offsets are converted. Responses are UTC (`…Z`). A literal `+` in a query string must be
URL-encoded (`%2B`); prefer `Z`. Defaults: `end` = now, `start` = `end − 24 h`. Validation (`422`):
malformed timestamps, `start >= end`, windows wider than `METRICS_MAX_QUERY_DAYS` (30), unsupported
query parameters (rejected, not ignored), and invalid `limit`/`cursor`/`status`/`interval`.

**Pagination is keyset (cursor) based**, not offset based. Rows are ordered `(checked_at DESC,
id DESC)` — the `id` tie-break makes the order total — and `next_cursor` encodes the last row's
position. Unlike offsets, this stays correct and fast while new rows arrive during paging (no
duplicates or skips) and never scans past skipped rows. The trade-offs: no total count (it would
need another scan) and no jumping to arbitrary page numbers. `limit` is 1–200 (default 50); pass
`next_cursor` back as `cursor` with the same filters.

```bash
# last 24 hours of history, newest first (default window and page size)
curl -s localhost:8000/api/v1/resources/<id>/checks
# failures in a specific window; fetch the next page with the returned cursor
curl -s "localhost:8000/api/v1/resources/<id>/checks?status=DOWN&start=2026-03-10T12:00:00Z&end=2026-03-10T14:00:00Z&limit=2"
curl -s "localhost:8000/api/v1/resources/<id>/metrics/summary?start=2026-03-10T12:00:00Z&end=2026-03-10T14:00:00Z"
curl -s "localhost:8000/api/v1/resources/<id>/metrics/timeseries?start=2026-03-10T12:00:00Z&end=2026-03-10T14:00:00Z&interval=5m"
```

History (`next_cursor` is `null` on the last page):

```json
{
  "resource_id": "6f9c1d1e-5a1e-4a52-9d52-2d6a0f4f2e11",
  "window": {"start": "2026-03-10T12:00:00Z", "end": "2026-03-10T14:00:00Z"},
  "items": [
    {"id": "…", "checked_at": "2026-03-10T13:06:00Z", "status": "DOWN", "http_status_code": null,
     "response_time_ms": null, "duration_ms": 10002.41, "error_category": "dns",
     "error_summary": "dns: hostname could not be resolved", "source": "SCHEDULED",
     "created_at": "2026-03-10T13:06:00.002Z"}
  ],
  "limit": 2,
  "next_cursor": "eyJ0IjogIjIwMjYtMDMtMTBUMTM6MDY6MDArMDA6MDAiLCAiaSI6ICIuLi4ifQ"
}
```

Summary:

```json
{
  "resource_id": "6f9c1d1e-5a1e-4a52-9d52-2d6a0f4f2e11",
  "window": {"start": "2026-03-10T12:00:00Z", "end": "2026-03-10T14:00:00Z"},
  "checks": {"total": 8, "healthy": 5, "degraded": 1, "down": 2},
  "availability_percent": 62.5,
  "latency": {"samples": 6, "avg_ms": 350.0, "min_ms": 100.0, "max_ms": 600.0,
              "p50_ms": 350.0, "p95_ms": 575.0},
  "first_checked_at": "2026-03-10T12:00:00Z",
  "last_checked_at": "2026-03-10T13:06:00Z"
}
```

Time series:

```json
{
  "resource_id": "6f9c1d1e-5a1e-4a52-9d52-2d6a0f4f2e11",
  "window": {"start": "2026-03-10T12:00:00Z", "end": "2026-03-10T14:00:00Z"},
  "interval": "5m",
  "buckets": [
    {"bucket_start": "2026-03-10T12:00:00Z", "total": 4, "healthy": 2, "degraded": 1, "down": 1,
     "availability_percent": 50.0, "avg_response_time_ms": 200.0, "p95_response_time_ms": 290.0}
  ]
}
```

### Availability and latency semantics

- **Availability is check-based, not continuous uptime.** `availability_percent = healthy_checks /
  total_checks × 100`, rounded half-up to two decimals. `DEGRADED` is *not* healthy. The raw counts
  are returned so the calculation is transparent. It measures the checks that happened; **nothing is
  inferred for the time between checks**, so a resource checked every minute is not "100% up for the
  whole hour". With no checks in the window, availability is **`null`** (unknown), not 0 and not 100.
  A window of real checks that all failed is `0.0`.
- **Latency statistics** (`avg`, `min`, `max`, `p50`, `p95`) use checks that **received an HTTP
  response**: `HEALTHY` and `DEGRADED`, which both have a measured `response_time_ms`. `DOWN` checks
  have no latency and are excluded; `samples` says how many checks contributed. Percentiles use
  PostgreSQL `percentile_cont` (linear interpolation). With no samples every latency value is `null`.
  Values are rounded to two decimals.
- All aggregation runs in PostgreSQL; the API never loads history rows to compute statistics.

### Time-series buckets

- `interval` is `1m`, `5m`, `15m`, `1h`, `1d`, or `auto` (default): the **finest** interval whose
  worst-case bucket count is within `METRICS_MAX_TIMESERIES_BUCKETS` (1000), e.g. 24 h gives `5m`,
  30 days gives `1h`. An explicit interval that would exceed the limit is rejected (`422`) with guidance.
- Buckets are **UTC-aligned** (`date_bin` from a midnight-UTC origin): 5-minute buckets start at
  `:00, :05, …`, daily buckets at 00:00 UTC. The first and last bucket may be partial: only checks
  inside `[start, end)` are counted.
- **Only buckets containing at least one check are returned.** A missing bucket means *no
  observations* in that period; **it does not mean the service was down** (the resource may have been
  disabled, the worker stopped, or the interval longer than the bucket). Empty buckets are never
  fabricated, so a request cannot return thousands of zero rows.
- Bucket totals sum to the summary totals for the same window (verified in tests and the smoke test).

### Retention

The worker deletes results older than **`METRICS_RETENTION_DAYS` (30)**, measured on the database clock:

- once at worker start, then every `METRICS_CLEANUP_INTERVAL_HOURS` (24); a failed run is retried
  within 15 minutes; nothing runs on ordinary scheduler ticks;
- in batches of `METRICS_CLEANUP_BATCH_SIZE` (1000), **each batch in its own short transaction** using
  `FOR UPDATE SKIP LOCKED`: it holds no long locks, does not block in-flight checks, and two workers
  take disjoint rows instead of conflicting;
- idempotent and restart-safe: committed batches stay deleted, an interrupted run continues next
  time, and a repeat finds nothing to do (an index on `checked_at` keeps the scan cheap);
- limited to `resource_check_results`: resource configuration and latest state are never touched;
- logged as `retention cleanup completed` with `deleted`, `batches`, and `duration_ms`.

**Migrations never delete data.** Retention is runtime policy: upgrading the schema does not purge
existing rows, and the first cleanup happens only when a worker runs under its configured policy.
`METRICS_MAX_QUERY_DAYS` may not exceed `METRICS_RETENTION_DAYS` (enforced at startup), so queries
cannot silently describe data that no longer exists.

### Performance

Indexes: `(resource_id, checked_at DESC, id DESC)` serves history, summaries, and series;
`(checked_at)` serves retention. Nothing else is indexed. A reproducible check on synthetic data
(3 resources × 8,640 checks = 25,920 rows) lives in `tests/test_integration_metrics_perf.py`
(`pytest tests/test_integration_metrics_perf.py -s`). It asserts via `EXPLAIN` that 24-hour history,
summary, and series queries use the composite index with no sequential scan, and prints timings.
On one development laptop (PostgreSQL in Docker) the API responded in roughly 30-110 ms, including a
30-day summary over 8,640 rows (about 33 ms) with under 1.5 MiB of Python allocation per request.
These are small local numbers, **not production benchmarks**.

## Scheduling and worker design

**Source of truth is PostgreSQL**, not memory. Each resource row holds `next_check_at` and a
lease (`lease_token`, `lease_owner`, `lease_expires_at`).

1. The worker loop polls (default every 2 s) and asks the database for resources that are
   enabled, due (`next_check_at <= now()`), and not under an unexpired lease. It only asks for
   as many as it has free concurrency slots.
2. Claiming is one atomic `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED)` that stamps
   a fresh lease token and expiry (`timeout + 30 s grace`). Competing claimers never block
   or receive the same row.
3. The check runs with **no database connection held**. It has a hard wall-clock deadline
   (the resource's timeout) covering DNS, connect, TLS, and response headers.
4. The result is written with `UPDATE … WHERE id = … AND lease_token = <mine>`, setting
   `next_check_at = now() + interval` (so slow checks never pile up) and clearing the lease,
   **and the history row is inserted in the same transaction**. If the row was deleted,
   disabled, or reconfigured meanwhile — or the lease expired and another executor took over —
   **zero rows match, the result is discarded, and no history is recorded**. A stale result can
   therefore never overwrite newer state or appear as a measurement.
5. All times use the **database clock** (`now()`), so processes agree on "due" regardless of
   their own clocks.

| Concern | Mechanism |
| --- | --- |
| Overlapping checks of one resource | Exclusive DB lease; plus an in-process guard |
| Scheduled vs manual overlap | Both claim the same lease |
| Duplicate scheduler iterations / accidental second worker | `SKIP LOCKED` + lease; tested with two competing schedulers |
| Bounded concurrency | At most `MONITORING_MAX_CONCURRENT_CHECKS` (default 10) checks in flight |
| Slow target blocks others | Each check is its own task with its own deadline |
| DB connection exhaustion | Sessions are short-lived and never held across HTTP calls |
| One failure kills the loop | Per-check and per-poll exceptions are logged; poll failures back off up to 30 s |
| Crashed worker | Lease expires after `timeout + grace`; the resource is reclaimed automatically |
| Restart | Nothing in memory matters; overdue resources are simply due |
| Graceful shutdown (SIGTERM) | Stop polling, let in-flight checks finish up to `MONITORING_SHUTDOWN_GRACE_SECONDS` (20 s), then cancel and release remaining leases |

**One worker.** The API never polls, so running several API processes cannot duplicate checks.
Run exactly one `worker` container; extra workers are *safe* (leases keep checks exclusive) but
unsupported and merely redundant. Multi-replica scheduling, sharding, and distributed
coordination are **out of scope** so far.

The worker's Docker health check passes while it keeps completing database polls
(`python -m app.worker --healthcheck`, a heartbeat file).

## Network safety (SSRF)

CloudPulse makes HTTP requests to URLs users supply, so it must not become a way to reach
internal infrastructure. The default policy:

- Allows only `http` and `https`; rejects malformed URLs, embedded credentials
  (`user:pass@`), whitespace/control characters, and IP-literal or numeric hosts
  (`127.0.0.1`, `2130706433`, `[::1]`); rejects `localhost`, `*.local`, `*.internal`, etc.
- **Never follows redirects**, and ignores proxy environment variables.
- **Validates what is actually connected to.** A hostname is resolved once per connection, *every*
  returned address must pass the policy (a mixed `[public, 127.0.0.1]` answer is rejected), and the
  socket is then opened to that validated IP — the HTTP client never re-resolves the name, which
  closes the DNS-rebinding time-of-check/time-of-use gap. TLS certificates are still verified
  against the original hostname. Anything uncertain fails closed.
- Blocks loopback, RFC 1918, CGNAT, unique-local, link-local, multicast, unspecified, reserved,
  and documentation ranges; unwraps IPv4-mapped, NAT64, and 6to4 IPv6 forms; and always blocks
  cloud-metadata addresses (`169.254.169.254`, `fd00:ec2::254`, …).
- A blocked target is recorded as `DOWN` with `blocked: target address is not permitted`.

**Local development opt-in.** To monitor a local or Docker-network service, list it in
`MONITORING_ALLOWED_LOCAL_HOSTS` (`host` or `host:port`, comma-separated). It is empty by default,
exempts **only** loopback/private ranges for **only** the listed hosts (metadata, link-local,
multicast are blocked even then), logs a warning at startup, and the application **refuses to
start** with it set when `ENVIRONMENT` is `staging` or `production`.

## Security and deployment limitations

- **There is no authentication yet.** Anyone who can reach the API can create, change,
  delete, and trigger checks on resources. **Do not expose the API to an untrusted or public
  network.** Compose publishes ports on `127.0.0.1` only; keep it that way until an
  authentication phase exists.
- The SSRF policy is defense in depth for a local portfolio system, not a substitute for network
  egress controls (run the worker in a network that cannot reach sensitive internal systems).
- One worker; no HA or distributed scheduling.
- History is kept for `METRICS_RETENTION_DAYS` (30) only; there is no downsampling, so long-term
  trends beyond retention are not available. Availability is check-based (not continuous uptime).
  There are no alerts or incidents yet.
- History grows by `resources × (86400 / interval)` rows per day; size retention and check
  intervals accordingly. PostgreSQL is used as-is (no partitioning or time-series extension).
- Checks are `GET` only, with response timing measured to headers; bodies are never read.

## Environment configuration

All configuration is via environment variables. [`.env.example`](.env.example) documents
every setting; copy it to `.env` (git-ignored). The same root `.env` is read by Docker Compose,
by the backend when run from `backend/`, and by Vite.

| Variable | Used by | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | API, worker | `local`, `test`, `development`, `staging`, or `production` |
| `LOG_LEVEL`, `LOG_FORMAT` | API, worker | Logging verbosity; `json` (default) or `text` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | all | Database name and credentials (password required) |
| `POSTGRES_HOST`, `POSTGRES_PORT` | host runs | Database location for a backend run on the host (default `127.0.0.1`; Compose sets `db`). Use `127.0.0.1`, not `localhost`: ports are published on IPv4 loopback only and some hosts try IPv6 first and stall |
| `DATABASE_URL` | API, worker | Optional full SQLAlchemy URL; overrides `POSTGRES_*` |
| `CORS_ORIGINS` | API | Comma-separated browser origins allowed to call the API |
| `MONITORING_MIN_INTERVAL_SECONDS` / `MAX_` | API | Interval bounds (10 / 86400) |
| `MONITORING_DEFAULT_INTERVAL_SECONDS` | API | Default interval for new resources (60) |
| `MONITORING_DEFAULT_TIMEOUT_SECONDS` / `MAX_` | API | Default / maximum timeout (10 / 30) |
| `MONITORING_LEASE_GRACE_SECONDS` | API, worker | Extra lease time beyond a check's timeout (30) |
| `MONITORING_ALLOWED_LOCAL_HOSTS` | API, worker | **Dev only** private-address allowlist (empty) |
| `MONITORING_POLL_INTERVAL_SECONDS` | worker | Database poll cadence (2) |
| `MONITORING_MAX_CONCURRENT_CHECKS` | worker, API | Max checks in flight (10) |
| `MONITORING_SHUTDOWN_GRACE_SECONDS` | worker | Drain time on shutdown (20) |
| `METRICS_RETENTION_DAYS` | worker | History older than this is deleted (30) |
| `METRICS_CLEANUP_INTERVAL_HOURS`, `METRICS_CLEANUP_BATCH_SIZE` | worker | Cleanup cadence (24 h) and rows per transaction (1000) |
| `METRICS_MAX_QUERY_DAYS` | API | Widest query window (30; must be ≤ retention) |
| `METRICS_DEFAULT_WINDOW_HOURS` | API | Window when `start`/`end` are omitted (24) |
| `METRICS_MAX_TIMESERIES_BUCKETS` | API | Maximum buckets per series response (1000) |
| `METRICS_DEFAULT_PAGE_SIZE`, `METRICS_MAX_PAGE_SIZE` | API | History page size (50 / 200) |
| `VITE_API_URL` | frontend | API base URL **as seen from the browser** (inlined at build time) |
| `*_HOST_PORT` | Compose | Host ports published on 127.0.0.1 |

## Local development without Docker

Run PostgreSQL in Docker and the apps natively for fast reloads:

```bash
docker compose up -d db
```

### Backend

```bash
cd backend
python -m venv .venv
# macOS/Linux:        source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload            # API:    http://localhost:8000
python -m app.worker                     # worker: run in a second terminal
```

Set `LOG_FORMAT=text` in `.env` for human-readable logs while developing.

### Frontend

```bash
cd frontend
npm ci
npm run dev                              # http://localhost:5173
npm run build && npm run preview         # production build preview
```

## Database migrations

Alembic reads the database URL from application settings (never from `alembic.ini`) and
targets `app.db.base.Base.metadata`. Revisions: `0001` empty baseline, `0002` the
`monitored_resources` table (config, latest observation, lease columns, constraints, indexes),
`0003` the `resource_check_results` history table (additive; existing rows are untouched).

```bash
cd backend
alembic upgrade head                         # apply all migrations
alembic current                              # show current revision
alembic history                              # list revisions
alembic revision --autogenerate -m "message" # create a migration from model changes
alembic check                                # fail if models and migrations diverge
alembic downgrade -1                         # roll back one revision
```

With Docker: `docker compose run --rm migrate`.

## Testing and linting

### Backend

```bash
cd backend
ruff check .              # lint
ruff format --check .     # formatting check (use `ruff format .` to fix)
pytest                    # unit tests: no database, no internet
```

The unit tests cover URL/address policy, the guarded network backend, the checker (against a
throwaway local HTTP server), the scheduler/executor (in-memory store), request validation,
window/bucket/cursor/rounding logic, and the retention loop.

**PostgreSQL integration tests** (lease/claim correctness, concurrency, history atomicity and
rollback, history/summary/series correctness against hand-computed data, retention, query plans and
performance, API behaviour, migrations, schema constraints) run when `TEST_DATABASE_URL` is set, and are skipped otherwise. They
**truncate tables and run migrations up and down**, so the database name must end in `_test` or
`_ci` — the suite refuses to run against anything else. Never point it at your development database.

```bash
docker compose up -d db
docker compose exec db createdb -U cloudpulse cloudpulse_test
cd backend
TEST_DATABASE_URL=postgresql+psycopg://cloudpulse:<password>@127.0.0.1:5432/cloudpulse_test pytest
# PowerShell: $env:TEST_DATABASE_URL="postgresql+psycopg://..."; pytest
```

The suite applies migrations itself and verifies models and migrations are in sync.

### End-to-end

`python scripts/smoke_test.py --restart-worker` drives a running stack (see the demo-target section
above): scheduled and manual checks reach history, summary and series match the recorded rows,
history survives a worker restart, disabling stops new measurements, and unsafe URLs stay blocked.

### Frontend

```bash
cd frontend
npm run lint              # ESLint
npm run format:check      # Prettier (use `npm run format` to fix)
npm test                  # Vitest + Testing Library
npm run typecheck         # TypeScript
npm run build             # type-check + production build
```

## Health and readiness endpoints

| Endpoint | Purpose | Depends on DB | Success | Failure |
| --- | --- | --- | --- | --- |
| `GET /health` | Liveness — the process is up | No | `200 {"status": "ok"}` | — |
| `GET /ready` | Readiness — can serve traffic | Yes (`SELECT 1`) | `200 {"status": "ready", "checks": {"database": "ok"}}` | `503 {"status": "unavailable", "checks": {"database": "unavailable"}}` |

Failure details are logged server-side and never returned. Every response carries an
`X-Request-ID` header. Monitoring log lines (`cloudpulse.monitoring`) include `resource_id`,
`target_host`, `check_source` (`SCHEDULED`/`MANUAL`), `check_status`, `error_category`,
`http_status_code`, `response_time_ms`, and `duration_ms` — never the full URL.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and pull request:

- **backend** — Ruff lint and format check; `alembic upgrade head` and `alembic check` against a
  PostgreSQL 17 service container; full Pytest including the integration tests.
- **frontend** — `npm ci`, ESLint, Prettier check, Vitest, production build.
- **docker** — builds the images, starts the full stack (with the demo target), waits for health,
  and runs `scripts/smoke_test.py --restart-worker` end to end (history, metrics, worker restart).

Nothing is deployed.

## Portability notes

- All dependencies are declared and pinned (`requirements*.txt`, `package-lock.json`);
  PostgreSQL runs in Docker.
- Configuration is environment-driven; `.env.example` is committed, `.env` never is.
- No absolute or user-specific paths; `.gitattributes` and `.editorconfig` keep LF line endings
  and consistent formatting across Windows, macOS, and Linux.
- Database data lives in a Docker named volume (`cloudpulse_postgres-data`), not in the repo.
  To move data between machines use `pg_dump`/`pg_restore`; otherwise re-run migrations.
- Bootstrapping a new machine: install Git + Docker, clone, `cp .env.example .env`, set
  `POSTGRES_PASSWORD`, `docker compose up --build`.
- Keep the working copy outside cloud-synced folders (e.g. OneDrive).

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Foundation: API skeleton, DB/migrations, logging, Docker, CI, dashboard shell | Done |
| 1 | Resource registry, scheduled HTTP checks, latest health, manual checks, SSRF protection | Done |
| 2 | Metrics ingestion and history: atomic per-check records, availability/latency APIs, time series, retention | **Current** |
| 3 | Alerting and incidents: rules, threshold/failure evaluation, incident lifecycle | Planned |
| 4 | Full React monitoring dashboard | Planned |
| 5 | Cloud deployment: Azure hosting, CD pipeline, infrastructure-as-code if justified | Planned |

Phase boundaries beyond the current phase are provisional and refined as each phase begins.
### Phase 3 considerations

Alert evaluation can read accepted measurements from `resource_check_results`: rows exist only for
accepted checks, and the per-resource index supports "last N checks" and "failures in the last X
minutes" queries. `repo.complete()` returns only after a measurement is committed, so a later
alert-state or incident write can join the same transaction. Evaluation itself, incidents, and
notifications are **not implemented**.
