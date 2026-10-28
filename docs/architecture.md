# CloudPulse Architecture

This document describes the architecture as implemented today (Phase 1) and the direction it
is designed to grow in. Anything under "Planned" does not exist yet.

## Current (Phase 1)

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
| `worker.py` | Worker process entry point: wires store, checker, scheduler; signals; heartbeat health check |
| `api` | Routers (`health`, `resources`), dependencies, domain-error → HTTP mapping |
| `schemas` | Pydantic request/response models (no lease internals exposed) |
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

One table, `monitored_resources`: configuration, the **latest** observation, scheduling
(`next_check_at`), and lease columns (`lease_token`, `lease_owner`, `lease_expires_at`).
Constraints enforce positive interval/timeout, `timeout <= interval`, status-code range, non-blank
name, and the status enum. A partial index on `next_check_at WHERE is_enabled` serves the
scheduler's due scan; `(created_at, id)` gives deterministic listing order.

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
3. **Complete** — `UPDATE … WHERE id = :id AND lease_token = :token`. `next_check_at` becomes
   `now() + interval` (database clock). No matching row ⇒ the result is discarded.

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

No authentication (do not expose the API); one worker; latest observation only; `GET`-only checks
with timing to response headers; the httpx transport hook above is an implementation dependency.

## Planned (later phases)

- **Phase 2 — history.** Persist each `CheckResult` as a measurement row (written in the same
  transaction as the latest-state update in `repo.complete`), then uptime and latency aggregation.
  Plan for retention/partitioning and indexes on `(resource_id, checked_at)`.
- **Phase 3 — alerting and incidents.** Rules evaluated over measurements; incident open/resolve
  lifecycle. Notification delivery will need its own failure handling.
- **Phase 4 — dashboard.** Resource management and status views in the React app.
- **Phase 5 — deployment.** Azure hosting and, if justified, infrastructure-as-code (see
  [`infra/`](../infra/README.md)); an authentication/authorization layer belongs before any
  public exposure.

New infrastructure (queues, caches, extra schedulers) will be introduced only when a phase
requires it, with the reasoning recorded here.
