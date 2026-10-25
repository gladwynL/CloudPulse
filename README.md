# CloudPulse

[![CI](https://github.com/gladwynL/CloudPulse/actions/workflows/ci.yml/badge.svg)](https://github.com/gladwynL/CloudPulse/actions/workflows/ci.yml)

**CloudPulse** is a cloud monitoring and observability platform being built as a
production-style engineering project: health checks, uptime and latency tracking,
alerting, and incident management for cloud services, with a React dashboard on top.

> **Current status: Phase 0 — Foundation.**
> This repository currently contains the engineering foundation only: API skeleton,
> database and migration tooling, structured logging, Docker, CI, and a dashboard shell.
> **No monitoring features are implemented yet.** See the [roadmap](#roadmap).

---

## Contents

- [Portfolio context](#portfolio-context)
- [Planned capabilities](#planned-capabilities)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Quick start (Docker Compose)](#quick-start-docker-compose)
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

## Planned capabilities

These are the goals for the finished system. **None are implemented in Phase 0.**

- Registry of monitored services and resources
- Configurable HTTP health checks
- Current health/status per resource
- Historical health and performance metrics; uptime and latency tracking
- Alert rules with threshold and failure detection
- Incident creation and resolution
- Monitoring REST APIs and a polished React dashboard
- Azure deployment, with infrastructure-as-code if justified

## Architecture

```
Browser ──► frontend (React/Vite, nginx) ──HTTP/CORS──► backend (FastAPI) ──► PostgreSQL
                                                               ▲
                                                 migrate (one-shot: alembic upgrade head)
```

- **frontend** — static React build served by nginx (Vite dev server locally).
- **backend** — FastAPI app built by a `create_app()` factory; SQLAlchemy 2 with a lazily
  created engine; JSON structured logs to stdout; `X-Request-ID` correlation.
- **migrate** — one-shot Compose service that applies Alembic migrations before the API starts.
- **db** — PostgreSQL 17 with a named volume for persistent local data.

Details and design decisions: [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Area           | Technology                                                      |
| -------------- | --------------------------------------------------------------- |
| Backend        | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, psycopg 3, Pydantic Settings |
| Database       | PostgreSQL 17                                                    |
| Frontend       | React 19, TypeScript, Vite                                       |
| Testing        | Pytest (backend); Vitest + Testing Library (frontend)            |
| Code quality   | Ruff (lint + format); ESLint + Prettier                          |
| Infrastructure | Docker, Docker Compose, GitHub Actions                           |

## Repository structure

```
CloudPulse/
├── backend/
│   ├── app/
│   │   ├── api/            # routers (routes/health.py) and shared dependencies
│   │   ├── core/           # settings, structured logging, request middleware
│   │   ├── db/             # engine/session management, declarative Base
│   │   ├── models/         # ORM models (none yet)
│   │   ├── schemas/        # Pydantic response models
│   │   ├── services/       # non-HTTP logic (database readiness check)
│   │   └── main.py         # create_app() factory
│   ├── alembic/            # migration environment + versions/ (baseline only)
│   ├── tests/              # Pytest suite (unit + optional integration)
│   ├── alembic.ini
│   ├── pyproject.toml      # project metadata, Ruff and Pytest config
│   ├── requirements.txt    # pinned runtime dependencies
│   ├── requirements-dev.txt
│   └── Dockerfile
├── frontend/
│   ├── src/                # App shell, components, status hook, API client, tests
│   ├── public/
│   ├── eslint.config.js
│   ├── vite.config.ts      # Vite + Vitest config
│   ├── nginx.conf
│   ├── package.json / package-lock.json
│   └── Dockerfile
├── infra/                  # placeholder for Phase 5 cloud infrastructure
├── docs/                   # architecture notes
├── .github/workflows/ci.yml
├── docker-compose.yml
├── .env.example
└── README.md
```

## Prerequisites

| Tool                         | Version  | Needed for                         |
| ---------------------------- | -------- | ---------------------------------- |
| Git                          | any      | everything                         |
| Docker Desktop / Engine + Compose v2 | recent | running the full stack        |
| Python                       | 3.12.x   | backend development without Docker |
| Node.js                      | 22+ (CI uses 24) | frontend development without Docker |

Only Git and Docker are required to run the stack.

## Quick start (Docker Compose)

```bash
git clone https://github.com/gladwynL/CloudPulse.git
cd CloudPulse
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
# edit .env and set POSTGRES_PASSWORD
docker compose up --build
```

| Service    | URL                                   |
| ---------- | ------------------------------------- |
| Frontend   | http://localhost:3000                 |
| API        | http://localhost:8000                 |
| API docs   | http://localhost:8000/docs            |
| PostgreSQL | `localhost:5432` (credentials from `.env`) |

Useful commands:

```bash
docker compose up --build -d        # start in the background
docker compose ps                   # service status and health
docker compose logs -f backend      # follow API logs (JSON lines)
docker compose run --rm migrate     # re-run migrations
docker compose down                 # stop (data is kept)
docker compose down -v              # stop and DELETE the local database volume
```

Startup order is enforced with health checks: `db` (healthy) → `migrate` (completed) →
`backend` (healthy) → `frontend`.

## Environment configuration

All configuration is via environment variables. [`.env.example`](.env.example) documents
every setting; copy it to `.env` (git-ignored) and adjust. The same root `.env` is read by
Docker Compose, by the backend when run from `backend/`, and by Vite.

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `local`, `test`, `development`, `staging`, or `production` |
| `LOG_LEVEL`, `LOG_FORMAT` | Logging verbosity; `json` (default) or `text` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Database name and credentials (password required) |
| `POSTGRES_HOST`, `POSTGRES_PORT` | Database location for a backend run on the host (Compose sets `db` itself) |
| `DATABASE_URL` | Optional full SQLAlchemy URL; overrides the `POSTGRES_*` components |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API |
| `VITE_API_URL` | API base URL **as seen from the browser** (inlined at build time) |
| `*_HOST_PORT` | Host ports Compose publishes; change these if a port is in use |

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
uvicorn app.main:app --reload            # http://localhost:8000
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
targets `app.db.base.Base.metadata`. Phase 0 ships a single empty **baseline** revision
(`0001`) — no application tables exist yet.

```bash
cd backend
alembic upgrade head                         # apply all migrations
alembic current                              # show current revision
alembic history                              # list revisions
alembic revision --autogenerate -m "message" # create a migration from model changes
alembic check                                # fail if models and migrations diverge
alembic downgrade -1                         # roll back one revision
```

With Docker: `docker compose run --rm migrate` (or `docker compose run --rm backend alembic current`).

## Testing and linting

### Backend

```bash
cd backend
ruff check .              # lint
ruff format --check .     # formatting check (use `ruff format .` to fix)
pytest                    # unit tests — no database required
```

Unit tests override the `get_db` dependency with stubs, so they never touch your local
database. Integration tests (`tests/test_integration_db.py`) run only when
`TEST_DATABASE_URL` is set — CI sets it against a PostgreSQL service container:

```bash
# with `docker compose up -d db` running and migrations applied:
TEST_DATABASE_URL=postgresql+psycopg://cloudpulse:<password>@localhost:5432/cloudpulse pytest
```

### Frontend

```bash
cd frontend
npm run lint              # ESLint
npm run format:check      # Prettier (use `npm run format` to fix)
npm test                  # Vitest + Testing Library
npm run typecheck         # TypeScript
npm run build             # type-check + production build
```

ESLint uses `eslint-config-prettier` so formatting is owned solely by Prettier.

## Health and readiness endpoints

| Endpoint | Purpose | Depends on DB | Success | Failure |
| --- | --- | --- | --- | --- |
| `GET /health` | Liveness — the process is up | No | `200 {"status": "ok"}` | — |
| `GET /ready` | Readiness — can serve traffic | Yes (`SELECT 1`) | `200 {"status": "ready", "checks": {"database": "ok"}}` | `503 {"status": "unavailable", "checks": {"database": "unavailable"}}` |

Failure details are logged server-side and never returned to clients. Every response
includes an `X-Request-ID` header. The frontend's **System status** panel calls both
endpoints to show the platform's own connectivity.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and every
pull request:

- **backend** — install, `ruff check`, `ruff format --check`, `alembic upgrade head` and
  `alembic check` against a PostgreSQL 17 service container, then `pytest` (including
  integration tests).
- **frontend** — `npm ci`, ESLint, Prettier check, Vitest, production build.
- **docker** — validates `docker-compose.yml` and builds both images.

Nothing is deployed in Phase 0.

## Portability notes

The project is designed to move between machines with no hidden state:

- All dependencies are declared and pinned (`requirements*.txt`, `package-lock.json`);
  PostgreSQL runs in Docker.
- Configuration is environment-driven; `.env.example` is committed, `.env` never is.
- No absolute or user-specific paths; all paths in config are relative to the repo.
- `.gitattributes` and `.editorconfig` enforce LF line endings and consistent formatting
  across Windows, macOS, and Linux.
- Database data lives in a Docker named volume (`cloudpulse_postgres-data`), not in the repo.
  To move data between machines, use `pg_dump`/`pg_restore`; otherwise just re-run migrations.
- Bootstrapping a new machine: install Git + Docker, clone, `cp .env.example .env`, set
  `POSTGRES_PASSWORD`, `docker compose up --build`.
- Keep the working copy outside cloud-synced folders (e.g. OneDrive) to avoid file-locking
  issues with `node_modules` and virtual environments.

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Foundation: API skeleton, DB/migrations, logging, Docker, CI, dashboard shell | **Current** |
| 1 | Monitored resources: data model and CRUD API for services/endpoints | Planned |
| 2 | Health checks: scheduled HTTP checks with status and latency capture | Planned |
| 3 | Metrics and uptime: history, uptime percentages, latency trends, dashboard views | Planned |
| 4 | Alerting and incidents: rules, threshold/failure evaluation, incident lifecycle | Planned |
| 5 | Cloud deployment: Azure hosting, CD pipeline, infrastructure-as-code if justified | Planned |

Phase boundaries beyond Phase 0 are provisional and will be refined as each phase begins.
