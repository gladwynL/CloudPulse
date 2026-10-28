# CloudPulse Architecture

This document describes the architecture as implemented today (Phase 2) and the direction it
is designed to grow in. Anything under "Planned" does not exist yet.

## Current (Phase 2)

```
 Browser / curl / Swagger
   │
   ▼
┌─────────────────────┐   HTTP   ┌──────────────────────────────┐   SQL   ┌────────────┐
│ frontend            │ ───────► │ backend (FastAPI)            │ ──────► │ PostgreSQL │
│ React shell (nginx) │          │ /health /ready               │         │            │
└─────────────────────┘          │ /api/v1/resources…           │         │ monitored_ │
                                 │ manual check ──► target URL  │         │ resources  │
                                 └──────────────────────────────┘         │ (+ leases) │
 migrate (one-shot) ── alembic upgrade head ─────────────────────────────►│            │
                                 ┌──────────────────────────────┐         │            │
                                 │ worker  (python -m app.worker)│ ──────► │            │
                                 │ poll ► claim ► check ► store │         └────────────┘
                                 └──────────────┬───────────────┘
                                                ▼
                                       monitored HTTP services
```

### Package layout (`backend/app`)

| Package | Responsibility |
| --- | --- |
| `main.py` | `create_app()` factory: settings, logging, CORS, middleware, error handlers, routers, checker lifecycle |
| `worker.py` | Worker process entry point: wires store, checker, scheduler and retention; signals; heartbeat health check |
| `api` | Routers (`health`, `resources`), dependencies, domain-error → HTTP mapping |
| `schemas` | Pydantic request/response models for resources and metrics (no lease internals exposed) |
| `services` | Resource rules (validation, update semantics), readiness check |
| `repositories` | All SQL: CRUD plus the lease/claim/complete/release protocol |
| `monitoring` | `security` (URL + address policy), `network` (guarded connections), `checker`, `executor`, `scheduler`, `store`, `types` |
| `models`, `db` | `MonitoredResource`, `ResourceStatus`; engine/session, declarative base |
| `core` | Settings, JSON logging, request-ID/logging middleware |

Dependencies point inward: `api → services → repositories → models`; `monitoring` is shared by the
API (manual checks) and the worker (scheduled checks), and depends only on `repositories`/`models`.
The scheduler and executor depend on a `CheckStore` protocol, so they are unit-tested against an
in-memory store and integration-tested against PostgreSQL.

### Data model

Two tables.

**`monitored_resources`**: configuration, the **latest** observation, scheduling (`next_check_at`),
and lease columns (`lease_token`, `lease_owner`, `lease_expires_at`). Constraints enforce positive
interval/timeout, `timeout <= interval`, status-code range, non-blank name, and the status enum. A
partial index on `next_check_at WHERE is_enabled` serves the scheduler's due scan; `(created_at, id)`
gives deterministic listing order.

**`resource_check_results`** (Phase 2): one immutable row per *accepted* check.
`resource_id` references `monitored_resources` with `ON DELETE CASCADE` (deleting a resource deletes
its history; orphans are impossible). Columns: `checked_at`, `status` (never UNKNOWN),
`http_status_code`, `response_time_ms`, `duration_ms`, `error_category`, `error_summary`, `source`
(`SCHEDULED`/`MANUAL`), `created_at`. Constraints reject negative times, out-of-range HTTP codes and
UNKNOWN. Two indexes only: `(resource_id, checked_at DESC, id DESC)` for history, summary and series
queries, and `(checked_at)` for retention deletes.

Time semantics: `checked_at` is the **event time** (when the result was accepted, database clock, the
transaction timestamp: identical to the resource's `last_checked_at` for that check); `created_at` is
physical insert time; `response_time_ms` is HTTP latency to response headers (null if none);
`duration_ms` is the execution time of the whole check. Numbers are `NUMERIC(10,2)`, rounded once in
Python. Only sanitized text is stored.

### Health states

`UNKNOWN` (no completed check) · `HEALTHY` (expected status) · `DEGRADED` (answered with another
status; redirects are not followed) · `DOWN` (could not complete: timeout, DNS, connection, TLS, or
blocked target). Error summaries are fixed strings per category; raw exception text never reaches
storage, API responses, or logs.

### Scheduling and consistency

The database is authoritative for *what is due* and *who is working on it*:

1. **Claim** — `UPDATE … WHERE id IN (SELECT … due … FOR UPDATE SKIP LOCKED LIMIT n)` stamps a
   fresh lease token and an expiry of `timeout + grace`. Manual checks claim a single id the same way.
2. **Check** — runs with no DB connection held, under a hard wall-clock deadline.
3. **Complete** — one transaction: `UPDATE … WHERE id = :id AND lease_token = :token` (latest state;
   `next_check_at` becomes `now() + interval` on the database clock), then, only if that matched a
   row, `INSERT` the history row, then commit. No matching row ⇒ the result is discarded and **no
   history is written**.

Because API updates (disable, reconfigure) clear the lease, and deletes remove the row, a check that
finishes afterwards matches nothing and cannot resurrect, overwrite, or misattribute state. A crashed
executor's lease simply expires and the resource is claimed again. Two workers started by mistake
cannot double-check a resource (verified by a test with two competing schedulers); the supported
deployment is a single worker because it is the simplest thing that is correct.

The worker loop is bounded (max concurrent checks), survives per-poll and per-check failures
(poll failures back off), drains in-flight checks on SIGTERM within a grace period, then cancels
and releases remaining leases.

**Why a separate worker rather than a scheduler inside the API?** An embedded scheduler runs once
per API worker process, so scaling the API would multiply every check. A dedicated process makes
"who polls" explicit, and it is the same image with a different command, so no code or dependency
is duplicated. **Why no Redis/Celery/APScheduler?** The state (due time, lease) already lives in the
database, which gives restart recovery and multi-process safety with one `UPDATE`; a broker would add
infrastructure without adding capability at this scale.

### Atomic history persistence

`repo.complete()` (called only by `SqlCheckStore`, for scheduled and manual checks alike) is the single
place a result becomes authoritative. The lease-guarded state `UPDATE` is the gate for the history
`INSERT`, and both are in one transaction, so:

- an insert failure rolls the state update back too (the lease then expires and the check is retried);
- discarded results (lost lease, deleted/disabled/reconfigured resource) leave no measurement;
- completing a claim twice, or two workers racing on one claim, records exactly one row (the second
  `UPDATE` re-evaluates against the committed row and matches nothing);
- the network request is outside the transaction: no lock or transaction spans the HTTP call.

There is no second history-writing path. These properties have real-PostgreSQL tests, including
concurrent completion and competing schedulers.

### Reading history

Everything is computed in PostgreSQL over half-open `[start, end)` windows on `checked_at`:

- **History**: keyset pagination on `(checked_at DESC, id DESC)`: stable under concurrent inserts, no
  deep-offset scans, no total count. The `id` tie-break is asserted on the statement itself because
  the index would otherwise mask its absence.
- **Summary**: one aggregate row (`count … FILTER`, `avg/min/max`, `percentile_cont`). Availability is
  check-based (`healthy / total`, DEGRADED not healthy, null when no checks); latency covers checks that
  received a response.
- **Time series**: `date_bin` buckets aligned to a UTC origin, only non-empty buckets returned (a gap
  means no observations, never downtime), worst-case bucket count bounded by configuration, totals
  consistent with the summary.

Query statements are built by functions in `repositories/check_results.py` so tests can `EXPLAIN` the
exact SQL the endpoints run.

### Retention

`RetentionTask` runs inside the worker (no new service): once at start, then every
`METRICS_CLEANUP_INTERVAL_HOURS`. It deletes results older than `METRICS_RETENTION_DAYS` on the database
clock in batches of `METRICS_CLEANUP_BATCH_SIZE`, one short transaction per batch, with
`FOR UPDATE SKIP LOCKED` so concurrent cleaners take disjoint rows. It is idempotent, restart-safe,
touches only the history table, and runs under runtime policy, never in a migration.

### Outbound request safety

User-supplied URLs are an SSRF surface, so the policy is enforced where it cannot be bypassed:

- At registration: scheme, credentials, IP literals, internal names, malformed hosts rejected (no DNS).
- At connection time: `GuardedNetworkBackend` (an httpcore network backend) resolves the host **once**,
  requires **every** returned address to pass classification, then connects to that validated IP. The
  HTTP client never resolves the name itself, which removes the DNS-rebinding race; TLS verification
  still uses the original hostname. Redirects are never followed; environment proxies are ignored.
- `MONITORING_ALLOWED_LOCAL_HOSTS` relaxes only loopback/private ranges for listed hosts, never
  metadata/link-local/multicast, and is rejected at startup in staging/production.

`HttpChecker` swaps the pool inside httpx's `AsyncHTTPTransport` (httpx offers no network-backend
hook). That is a private attribute, so httpx is pinned and the checker tests exercise the real
transport end to end (including a real TLS-handshake failure and DNS-rebinding scenarios).

### Observability

JSON logs to stdout. Monitoring events (`cloudpulse.monitoring`) carry `resource_id`, `target_host`
(never the full URL), `check_source`, `check_status`, `error_category`, `http_status_code`,
`response_time_ms`, `duration_ms`, `stored`. The HTTP client's own request logging is disabled
because it prints full URLs. Idle scheduler ticks log nothing; claims and completions do.

### Known limitations

No authentication (do not expose the API); one worker; history retained for a bounded period only
(no downsampling, so no long-term trends); availability is check-based, not continuous uptime; plain
PostgreSQL tables without partitioning (adequate for portfolio scale, to be revisited if volume grows);
`GET`-only checks with timing to response headers; the httpx transport hook above is an implementation
dependency.

## Planned (later phases)

- **Phase 3 — alerting and incidents.** Rules evaluated over accepted measurements (the history
  index supports "last N checks" and "failures in the last X minutes"); incident open/resolve
  lifecycle, ideally written in the same transaction as the measurement that triggers it. Notification
  delivery will need its own failure handling.
- **Phase 4 — dashboard.** Resource management and status views in the React app.
- **Phase 5 — deployment.** Azure hosting and, if justified, infrastructure-as-code (see
  [`infra/`](../infra/README.md)); an authentication/authorization layer belongs before any
  public exposure.

New infrastructure (queues, caches, extra schedulers) will be introduced only when a phase
requires it, with the reasoning recorded here.
