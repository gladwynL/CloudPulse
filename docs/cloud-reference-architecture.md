# Cloud reference architecture (design only)

> **Status: a design document. Nothing described here has been built, provisioned or paid for.**
> No cloud account exists for this project, no infrastructure-as-code has been written for it, and no
> resource in this document has ever been created. What *is* implemented and verified is the local
> deployment described in the [README](../README.md#deployment-local-production-style) and
> [`architecture.md`](architecture.md). This document records how the same system could move to a
> cloud later, so that decision can be made deliberately, with a budget, by the project owner.

## 1. Starting point: what CloudPulse is today

Five containers on one Compose network: an nginx-served React SPA, a FastAPI API, a single polling
worker, PostgreSQL, and a one-shot migration job. State lives only in PostgreSQL. The worker makes
outbound HTTP checks; the API makes them only for manual checks. The API has **no authentication**.

That last sentence decides the order of everything below.

## 2. Hard prerequisites before anything is public

1. **Authentication and authorization.** Today any caller can create, edit and delete resources and rules and
   trigger outbound requests. Before the API is reachable from the internet it needs, at minimum:
   authenticated identities, role checks on every mutating endpoint, an audit trail (the incident timeline's
   `MANUAL` actor would become a real user), and rate limits. This is a feature phase of its own, not a
   deployment detail. An identity provider (managed or self-hosted OIDC) is preferable to hand-rolled
   password storage.
2. **A budget decision.** Every managed database and container platform has a price once any free
   allowance ends, and free tiers can convert to paid ones. Pick a spending cap and alerts before creating
   anything.
3. **An egress policy for the worker.** The worker deliberately fetches user-supplied URLs. The in-app SSRF
   guard is defence in depth; in a cloud the worker must also run in a network that cannot reach metadata
   endpoints or internal services (see section 5).

## 3. Target shape

```
                      Internet
                         │ HTTPS (managed certificate)
                         ▼
   ┌───────────────────────────────────────────┐
   │ Edge: static hosting + CDN for the SPA     │   (public, caches immutable assets)
   │ /api/* ──► API ingress (WAF/rate limiting) │
   └──────────────────────┬────────────────────┘
                          │ private network
        ┌─────────────────┼──────────────────────┐
        ▼                 ▼                       ▼
  ┌───────────┐     ┌───────────┐          ┌──────────────┐
  │ API       │     │ Worker    │ ───────► │ monitored    │
  │ (N copies)│     │ (1 copy)  │  egress  │ services     │
  └─────┬─────┘     └─────┬─────┘  policy  └──────────────┘
        │                 │
        └────────┬────────┘
                 ▼
        ┌──────────────────┐     ┌──────────────────┐
        │ PostgreSQL       │     │ Secrets manager   │
        │ (managed, private)│    │ + log/metric sink │
        └──────────────────┘     └──────────────────┘
```

On Azure the natural mapping would be: **Static Web Apps or Blob + CDN** for the SPA; **Container Apps** for
the API and worker (a revision with min = max = 1 replica for the worker); **Azure Database for PostgreSQL
Flexible Server** on a private endpoint; **Key Vault** for secrets with managed identities; **Log Analytics**
for logs; **Container Registry** for images; **Entra ID / External ID** for authentication. Equivalents exist
elsewhere; none of this is specific to Azure.

## 4. Component by component

| Concern | Design | Trade-offs and notes |
| --- | --- | --- |
| **Frontend hosting** | The Vite build is static files. Host them on a static host/CDN with SPA fallback to `index.html`, immutable caching for `/assets/`, and `no-cache` for `index.html`. Set `VITE_API_URL=same-origin` (the SPA already supports it) and route `/api/*` to the API at the edge. | Same-origin avoids CORS and cookie complications. A separate API origin is possible but needs CORS and care. The hardened local nginx is a working model of the routing. |
| **API** | The existing image, stateless, 2+ replicas behind the ingress, `/health` for liveness and `/ready` for readiness (already implemented). `API_DOCS_ENABLED=false`, `ENVIRONMENT=production` (the API already refuses placeholder passwords and the private-address allowlist in that mode). | Scaling the API is safe: it never polls. Connection pools multiply by replica count, so size `DB_POOL_SIZE` against the database's connection limit. |
| **Worker** | The same image running `python -m app.worker`, **exactly one replica**. Leases make extra copies safe but unsupported, and one worker is the tested configuration. Restart policy always; the existing heartbeat health check marks it unhealthy when it cannot reach the database. | Single worker is a throughput ceiling and a brief gap during restarts (leases expire and checks resume, verified locally). Scaling out means testing multi-worker leasing at load first; the design (row leases) is ready for it but it has not been proven. |
| **Database** | Managed PostgreSQL 17 on a private network, TLS required, automated backups with point-in-time recovery, storage alerts. Migrations run as a release step (the one-shot `migrate` job) before new API/worker revisions receive traffic. | Managed service costs money from the first hour; self-hosting on a VM is cheaper but makes backups, patching and failover your problem. The local `db_backup.py` workflow is the manual equivalent of PITR. |
| **Secrets** | Database credentials in the platform's secret store, injected as environment variables via managed identity; none in images, repos or the SPA bundle (which is public). Rotate by creating a new credential, updating the secret, restarting. | The application only needs `POSTGRES_*` or `DATABASE_URL`. Never put anything secret in `VITE_*`. |
| **Networking** | The database and worker have no public endpoint. Only the edge and API ingress are reachable. The worker's egress goes through a NAT or firewall with a fixed address and a deny rule for metadata and private ranges. | The SSRF guard resolves and pins addresses per request, but a network-level deny is the control that holds if that code ever has a bug. |
| **Ingress and TLS** | Managed certificates, HTTPS only, HSTS, HTTP to HTTPS redirect. Keep the security headers nginx sends today (CSP, frame denial, nosniff). | A WAF adds cost and tuning effort; rate limiting at the ingress is the part worth having early. |
| **Authentication** | See section 2. OIDC login for the SPA (authorization-code flow with PKCE, no tokens in `localStorage`), bearer-token validation in the API, roles for read-only vs. manage. | The largest piece of new work in this whole design. |
| **Logging and monitoring** | The API and worker already log JSON to stdout with request ids and never log URLs' query strings. Collect stdout centrally; alert on worker health, `/ready` failures, error rate and database storage. Optionally have CloudPulse monitor its own public endpoint from a *different* provider, since a monitor that shares fate with the thing it monitors cannot tell you it is down. | Don't deploy a heavy observability stack just to watch this one; the built-in health endpoints and platform metrics are enough at this size. |
| **Backups** | Provider-managed automated backups plus periodic logical dumps (`pg_dump`) to separate storage, with a *tested* restore into a disposable database (as `db_backup.py verify` does locally). | An untested backup is a hope. Dumps contain monitored URLs, so encrypt and restrict them. |
| **CI/CD** | Keep the existing CI as the gate. Add: build and push images tagged with the commit SHA, deploy to a staging environment, run the smoke test and Playwright suite against it, then promote the same image digest to production. Use short-lived OIDC federation from CI to the cloud, never stored cloud keys. | Deploy only from `main` after all jobs pass; make rollback a redeploy of the previous digest. Database migrations must stay backward compatible for one release so rollback is safe. |
| **Scaling** | API: horizontal, by CPU or request rate. Worker: vertical first (`MONITORING_MAX_CONCURRENT_CHECKS`), multi-worker only after load testing. Database: the history table grows by `resources × 86400 / interval` rows per day; retention (30 days) bounds it, and partitioning is the next step if volume demands. | Measured locally at about 190 MiB for the whole stack; the first cloud bottleneck would be the database, not compute. |

## 5. What would need to change in the code

- **Authentication layer** (section 2), including SPA login and per-route authorization.
- **Per-tenant data model** if more than one team will use it (resources currently belong to nobody).
- **Metrics export** (Prometheus format or a platform agent) if the platform's own dashboards should show
  worker throughput and check latency; today these are in logs and the API.
- **Graceful multi-worker operation** once load-tested.

No change would be needed to the schema, the alert engine, the SSRF policy or the health endpoints.

## 6. Cost and risk summary

| Item | Cost risk | Mitigation |
| --- | --- | --- |
| Managed PostgreSQL | Billed continuously, even idle | Smallest burstable tier, or a self-managed container on one small VM |
| Container platform | Billed per vCPU/memory-second, with free grants that can lapse | Fixed replica counts, a hard budget alert, no autoscaling above a stated maximum |
| Egress | The worker's outbound traffic is billable beyond allowances | Bound `MONITORING_MAX_CONCURRENT_CHECKS` and intervals |
| Logs | Ingestion is billed per GB | JSON logs are small; set retention and daily caps |
| Free tiers | Some convert to paid automatically at the end of a trial | Do not use trial credits; confirm the plan cannot auto-upgrade |

**Recommendation.** Until the owner decides on a budget and authentication exists, keep CloudPulse
running locally and publish only the static showcase. That costs nothing, exposes nothing, and shows the
engineering. If a real deployment is later wanted, the first step is the authentication phase, not an
infrastructure one.

## 7. Implemented vs designed

| Implemented and verified | Designed only (this document) | Not implemented |
| --- | --- | --- |
| Containers, health checks, hardened local profile, same-origin proxy mode, backup, verify and restore, CI with a smoke test and browser tests, static showcase workflow | Everything in sections 3 to 6 | Any cloud account or resource, infrastructure-as-code, authentication, a public API, cloud CI/CD, managed secrets, a WAF |
