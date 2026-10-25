# CloudPulse Architecture

This document describes the architecture as implemented today (Phase 0) and the
direction it is designed to grow in. Anything under "Planned" does not exist yet.

## Current (Phase 0)

```
 Browser
   │  http://localhost:3000 (Docker)  /  http://localhost:5173 (Vite dev)
   ▼
┌──────────────────────┐        ┌──────────────────────────┐        ┌──────────────┐
│ frontend             │  HTTP  │ backend (FastAPI)        │  SQL   │ db           │
│ React + TS + Vite    │ ─────► │ /health  /ready          │ ─────► │ PostgreSQL 17│
│ static build / nginx │  CORS  │ SQLAlchemy 2 · Alembic   │        │ named volume │
└──────────────────────┘        └──────────────────────────┘        └──────────────┘
                                          ▲
                                ┌─────────┴─────────┐
                                │ migrate (one-shot)│  alembic upgrade head
                                └───────────────────┘
```

### Backend layering

| Package        | Responsibility                                                         |
| -------------- | ---------------------------------------------------------------------- |
| `app/main.py`  | `create_app()` factory: settings, logging, CORS, middleware, routers   |
| `app/core`     | Settings (`pydantic-settings`), structured logging, request middleware |
| `app/api`      | Routers and shared FastAPI dependencies (`DbSession`)                  |
| `app/services` | Logic independent of HTTP (e.g. the database readiness check)          |
| `app/schemas`  | Pydantic request/response models                                       |
| `app/db`       | Lazy engine, session factory, `get_db` dependency, declarative `Base`  |
| `app/models`   | ORM models — empty in Phase 0; imported by Alembic for autogenerate    |

Key decisions:

- **Liveness vs readiness.** `/health` never touches dependencies, so an orchestrator
  will not restart a healthy process just because PostgreSQL is briefly down.
  `/ready` runs `SELECT 1` and returns `503` when the database is unreachable, so
  traffic can be withheld instead. Error details are logged, never returned.
- **Lazy engine.** Importing the app opens no connections; tests and `/health` are
  database-independent. Tests override `get_db` rather than patching globals.
- **Migrations are explicit.** A one-shot `migrate` Compose service runs
  `alembic upgrade head` before the API starts, instead of migrating on every boot.
  This maps directly to a pre-deploy job in a cloud pipeline.
- **Structured logs to stdout.** Standard-library `logging` with a small JSON
  formatter; one object per line with `timestamp`, `level`, `logger`, `message`,
  `service`, `environment`, plus request fields (`request_id`, `method`, `path`,
  `status_code`, `duration_ms`). Suited to Docker log drivers and Azure Log Analytics.
- **Request correlation.** Every response carries `X-Request-ID` (propagated if the
  caller sent one), and the same ID appears in the request log line.
- **Configuration.** All settings come from environment variables; the repo-root
  `.env` is shared by Compose, the backend, and Vite. Secrets are never committed.

## Planned (later phases)

The roadmap below is directional and will be refined phase by phase.

- **Monitored resources** — registry of services/endpoints to watch.
- **Health checks** — configurable HTTP checks executed on a schedule, recording
  status codes and latency.
- **Metrics & uptime** — historical results, uptime percentages, latency trends.
- **Alerting & incidents** — threshold/failure rules, incident open/resolve lifecycle.
- **Dashboard** — resource status, charts, alerts, and incidents in the React app.
- **Cloud deployment** — Azure hosting and, if justified, infrastructure-as-code (see
  [`infra/`](../infra/README.md)).

New infrastructure (schedulers, workers, queues, caches) will be introduced only
when a phase requires it, with the reasoning recorded here.
