# nanny — stateless / Kubernetes architecture

Target: run nanny as **stateless replicas** behind a Service on Kubernetes/OpenShift,
with **CloudNativePG (CNPG)** Postgres as the single source of durable state. The
web console and the triage brain hold no authoritative state in process memory — any
pod can serve any request, and pods can be killed/scaled freely.

```
                      ┌─────────────── OpenShift / Kubernetes ───────────────┐
   Alertmanager  ──▶  │  Route ─▶ Service ─▶  nanny Deployment (N replicas)   │
   Icinga        ──▶  │                         • web console + API           │
   (webhooks,         │                         • triage brain (LLM/none)     │
    HMAC-signed)      │                         • paging reconciler (each pod) │
                      │                                │                       │
   Operators    ──▶   │                                ▼                       │
   (browser/CLI)      │                       CNPG Postgres Cluster (HA)       │
                      └───────────────────────────────┼───────────────────────┘
   Pod stdout (JSON) ─▶ OpenShift logging / Vector ─▶ Loki/Elasticsearch/SIEM
```

## What moved out of process memory

| Was (single-file) | Now (Postgres table) |
|---|---|
| `_active` dict (firing incidents) | `incidents` |
| `_operators` dict (sessions) | `sessions` |
| `history.json` | `history` |
| `incidents.jsonl` | `incident_log` |
| `_LOG` deque (activity/audit) | `activity_log` |
| `_jobs` dict | `jobs` |

Per-pod, non-authoritative caches stay in memory (fine to lose): the Icinga fleet
snapshot (`_FLEET_CACHE`, TTL'd) and the login rate-limiter counters.

Backend is chosen at startup: **`DATABASE_URL` set → PostgresStore; unset →
MemoryStore** (the original file/in-memory behaviour, for local/dev). Same `Store`
interface either way (`nanny/store.py`), so the rest of the code is backend-agnostic.

## The hard part: paging without a per-incident thread

The single-file version sleeps a thread per incident (`inc["cancel"].wait(hold)`)
then pages. That can't survive a stateless, multi-replica world (the pod holding the
timer might die, or a *different* pod might receive the resolve).

**Stateless design — a reconciler loop every replica runs:**

1. On a firing alert: `INSERT … ON CONFLICT (fingerprint) DO NOTHING` with
   `status='firing'`, `page_at = now() + hold`, `paged=false`. (Idempotent — duplicate
   webhooks/retries collapse to one incident.) Triage runs in the receiving pod and
   `UPDATE`s the row with analysis/audit/ticket.
2. Resolve → `UPDATE … SET status='resolved'` (and copy to `incident_log`). Claim/ack
   → set `owner`/`acked`. Either way the row no longer qualifies for paging.
3. Every few seconds, **each** replica runs the claim-and-page query:

   ```sql
   UPDATE incidents SET paged = true, phase = 'paged', updated = now()
   WHERE fingerprint IN (
     SELECT fingerprint FROM incidents
      WHERE status = 'firing' AND paged = false AND acked = false AND owner IS NULL
        AND page_at IS NOT NULL AND page_at <= now()
      FOR UPDATE SKIP LOCKED
      LIMIT 25)
   RETURNING *;
   ```

   `FOR UPDATE SKIP LOCKED` means concurrent replicas never grab the same row, and the
   `UPDATE … RETURNING` makes "decide to page" and "mark paged" atomic — so on-call is
   **paged exactly once**, with no leader election and no external scheduler.

The returned rows are the ones the pod then actually pages (PagerDuty) + comments on
the ticket. A crash between the UPDATE and the PagerDuty call is the one at-most-once
gap; acceptable (and detectable via the audit log) — paging twice is worse than a
rare retry, and Alertmanager/Icinga will re-fire if truly unhandled.

## Statelessness checklist

- No local files for authoritative state (only ephemeral caches).
- No in-process timers/threads that own incident lifecycle (→ reconciler).
- Session token validated against `sessions` on every request (already header-based).
- Readiness/liveness on `/healthz`; readiness also checks DB connectivity.
- Config + secrets from env (12-factor); `DATABASE_URL` from the CNPG secret.
- Schema applied idempotently at startup (`db/schema.sql`).

## Deployment (Helm, see `deploy/helm/nanny`)

- `Deployment` — ≥2 replicas, non-root (uid 10001, already enforced), probes, HPA.
- `Service` + OpenShift `Route` (edge TLS).
- CNPG `Cluster` CR (3 instances) → generates the `*-app` secret nanny reads as
  `DATABASE_URL`.
- `Secret`/`values.yaml` for auth mode, webhook token, LLM backend, log forwarding.

## Phasing

1. **Storage layer + schema** ✅ — `Store` interface, MemoryStore, PostgresStore.
2. **Package split** ✅ — `nanny/` modules.
3. **Wire state through the store + reconciler** ✅ — in-memory dicts/hold-thread
   replaced by `STORE` + `_reconciler_loop`; `/readyz` checks DB connectivity.
4. **Helm chart + CNPG** ✅ — `deploy/helm/nanny` (Deployment/Service/Route/Ingress,
   ConfigMap+Secret, CNPG `Cluster`, HPA). `helm lint`/`template` clean. **Still to
   verify on a real cluster:** PostgresStore against live CNPG, and an end-to-end
   multi-replica exactly-once paging run.
