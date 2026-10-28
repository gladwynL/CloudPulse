# CloudPulse

[![CI](https://github.com/gladwynL/CloudPulse/actions/workflows/ci.yml/badge.svg)](https://github.com/gladwynL/CloudPulse/actions/workflows/ci.yml)

**CloudPulse** is a cloud monitoring and observability platform being built as a
production-style engineering project: health checks, uptime and latency tracking,
alerting, and incident management for cloud services, with a React dashboard on top.

> **Current status: Phase 1 — Resource monitoring and latest health.**
> You can register HTTP services, have a worker check them automatically on a schedule,
> trigger manual checks, and read each resource's **latest** health through a REST API.
>
> **Not implemented yet:** historical metrics, uptime/latency trends, alert rules,
> incidents, notifications, authentication, the full monitoring dashboard, and cloud
> deployment. Phase 1 stores only the most recent observation per resource.
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
| Manual on-demand checks | ✅ Phase 1 |
| SSRF protections for user-supplied URLs | ✅ Phase 1 |
| Structured JSON logging, health/readiness probes, Docker, CI | ✅ Phase 0 |
| Historical measurements, uptime and latency trends | Planned (Phase 2) |
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
  and stores each result.
- **db** — PostgreSQL 17. It is the source of truth for configuration, latest results,
  *and* scheduling state (`next_check_at` plus a per-resource lease).
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
│   │   ├── models/         # MonitoredResource, ResourceStatus
│   │   ├── monitoring/     # checker, SSRF policy + guarded network backend,
│   │   │                   #   executor, scheduler, lease store
│   │   ├── repositories/   # SQL: CRUD and the lease/claim protocol
│   │   ├── schemas/        # Pydantic request/response models
│   │   ├── services/       # resource rules, readiness check
│   │   ├── main.py         # create_app() factory
│   │   └── worker.py       # `python -m app.worker`
│   ├── alembic/            # migrations (0001 baseline, 0002 monitored resources)
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
python scripts/smoke_test.py
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
   `next_check_at = now() + interval` (so slow checks never pile up) and clearing the lease.
   If the row was deleted, disabled, or reconfigured meanwhile — or the lease expired and
   another executor took over — **zero rows match and the result is discarded**. A stale
   result can therefore never overwrite newer state.
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
coordination are **out of scope** for Phase 1.

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

- **There is no authentication in Phase 1.** Anyone who can reach the API can create, change,
  delete, and trigger checks on resources. **Do not expose the API to an untrusted or public
  network.** Compose publishes ports on `127.0.0.1` only; keep it that way until an
  authentication phase exists.
- The SSRF policy is defense in depth for a local portfolio system, not a substitute for network
  egress controls (run the worker in a network that cannot reach sensitive internal systems).
- One worker; no HA or distributed scheduling.
- Only the **latest** observation is stored — no history, trends, uptime percentages, alerts, or
  incidents.
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
`monitored_resources` table (config, latest observation, lease columns, constraints, indexes).

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
throwaway local HTTP server), the scheduler/executor (in-memory store), and request validation.

**PostgreSQL integration tests** (lease/claim correctness, concurrency, API behaviour, migrations,
schema constraints) run when `TEST_DATABASE_URL` is set, and are skipped otherwise. They
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

`python scripts/smoke_test.py` drives a running stack (see the demo-target section above).

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
`target_host`, `check_source` (`scheduled`/`manual`), `check_status`, `error_category`,
`http_status_code`, `response_time_ms`, and `duration_ms` — never the full URL.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and pull request:

- **backend** — Ruff lint and format check; `alembic upgrade head` and `alembic check` against a
  PostgreSQL 17 service container; full Pytest including the integration tests.
- **frontend** — `npm ci`, ESLint, Prettier check, Vitest, production build.
- **docker** — builds the images, starts the full stack (with the demo target), waits for health,
  and runs `scripts/smoke_test.py` end to end.

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
| 1 | Resource registry, scheduled HTTP checks, latest health, manual checks, SSRF protection | **Current** |
| 2 | Historical measurements: per-check records, uptime and latency trends | Planned |
| 3 | Alerting and incidents: rules, threshold/failure evaluation, incident lifecycle | Planned |
| 4 | Full React monitoring dashboard | Planned |
| 5 | Cloud deployment: Azure hosting, CD pipeline, infrastructure-as-code if justified | Planned |

Phase boundaries beyond the current phase are provisional and refined as each phase begins.
Phase 2 builds on this one at two seams: the executor's `CheckResult` is the record to persist
as a measurement (inside the same transaction as the latest-state update in `repo.complete`),
and the lease/scheduling model carries over unchanged.
