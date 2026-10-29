# CloudPulse: portfolio summary

Content for a personal portfolio or CV. Everything here is true of the repository as it stands;
measured numbers carry their conditions.

## Project card (short)

CloudPulse is a monitoring, alerting and incident-management platform for HTTP services. A worker
checks services on a schedule, stores their history with availability and latency statistics, and turns
bad checks into incidents through configurable alert rules. Built with Python, FastAPI, PostgreSQL and
React/TypeScript, and runnable with one `docker compose up`.

## Technical highlights

- **Queue-free scheduling on PostgreSQL.** Row leases claimed with `FOR UPDATE SKIP LOCKED` give
  exclusive, crash-recoverable checks without Redis or Celery; verified by restarting the worker and by
  stopping the database under a running one (bounded backoff, health check flips to unhealthy, then
  recovers on its own).
- **Transactional integrity.** A check's result, history row, alert counters and any incident are written
  in one transaction with a fixed lock order; a partial unique index lets the database itself guarantee at
  most one active incident per rule. Concurrency behaviour is tested against real PostgreSQL.
- **SSRF-resistant outbound checks.** Registration-time validation plus connect-time address checks and
  pinning (so DNS rebinding cannot redirect a check to a private or cloud-metadata address), verified TLS,
  no redirect following, and sanitized errors.
- **Hardened, verified operations.** A Compose profile that publishes one loopback port, runs read-only
  containers with capabilities dropped behind an nginx proxy, plus checksummed backups proven by restoring
  into a disposable database, and a restore that refuses to overwrite data.
- **Tested the way a user uses it.** Playwright drives the real dashboard against the Docker stack through
  the whole alert-to-recovery loop, with axe-core WCAG A/AA checks and console-error audits, on free CI.

## Technology tags

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 17 · React 19 · TypeScript · Vite ·
TanStack Query · Recharts · Docker Compose · nginx · GitHub Actions · GitHub Pages · Pytest · Vitest ·
Playwright · axe-core

## Links

- Source: <https://github.com/gladwynL/CloudPulse>
- Public showcase (static page with real screenshots): <https://gladwynl.github.io/CloudPulse/>

The application itself runs locally and is not publicly hosted (it has no authentication); the showcase is
a static page.

## Resume bullets

- Built a monitoring and incident-management platform (FastAPI, PostgreSQL, React/TypeScript, Docker
  Compose) that schedules HTTP health checks with PostgreSQL row leases, stores history with
  availability and latency percentiles, and opens, acknowledges and auto-resolves incidents from alert
  rules.
- Designed concurrency-safe persistence: one transaction per check covering result, history, alert
  counters and incidents, a fixed lock order, and a partial unique index enforcing a single active incident
  per rule; validated with real-PostgreSQL concurrency tests (764 backend tests).
- Implemented SSRF defences with connect-time DNS pinning, TLS verification and error sanitization, and a
  hardened Docker deployment (single loopback port, read-only containers, dropped capabilities) with
  checksummed, restore-verified PostgreSQL backups.
- Automated quality on free GitHub Actions runners: lint, dependency audits, backend/frontend suites,
  a full-stack smoke test, and Playwright browser tests with axe-core accessibility checks; published a
  static project showcase to GitHub Pages.

*Measured (one Windows laptop, Docker Desktop, hardened stack with one monitored service): about 190 MiB of
memory at idle across all containers; API p50 latency 6-13 ms through nginx. These are local measurements,
not production benchmarks.*
