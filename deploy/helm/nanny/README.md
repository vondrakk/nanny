# nanny Helm chart

Deploys **nanny** as stateless replicas on Kubernetes/OpenShift with a
**CloudNativePG** Postgres backend. Every pod runs the paging reconciler; all
durable state lives in Postgres, so pods scale and restart freely. Design:
[`../../../ARCHITECTURE.md`](../../../ARCHITECTURE.md).

## Prerequisites

- A nanny container image (built from the repo's lean `Containerfile`) in a registry.
- **CloudNativePG operator** installed cluster-wide (provides the `Cluster` CRD) when
  `postgres.enabled=true` (the default). Install it once per cluster, e.g.
  `kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.24/releases/cnpg-1.24.0.yaml`.
- On OpenShift the bundled `Route` is used; on vanilla k8s set `route.enabled=false`
  and either `ingress.enabled=true` or port-forward.

## Install

```sh
helm install nanny ./deploy/helm/nanny -n nanny --create-namespace \
  --set image.repository=quay.io/yourorg/nanny \
  --set auth.password='<console-password>' \
  --set webhookToken='<alertmanager/icinga shared secret>'
```

GitOps-friendly (no secrets in values): pre-create one Secret with every sensitive
env var (`NANNY_AUTH_PASSWORD`, `NANNY_WEBHOOK_TOKEN`, `ANTHROPIC_API_KEY`,
`ATLASSIAN_API_TOKEN`, `SLACK_BOT_TOKEN`, `PAGERDUTY_API_TOKEN`, `ICINGA_API_PASSWORD`, …)
and reference it:

```sh
helm install nanny ./deploy/helm/nanny -n nanny --set existingSecret=nanny-secrets
```

`DATABASE_URL` is **not** part of that secret — it comes from the CNPG-generated
`<release>-nanny-db-app` secret's `uri` key automatically.

## How it fits together

| Concern | Resource |
|---|---|
| Stateless app, ≥2 replicas, non-root, `/healthz` + `/readyz` probes | `Deployment` |
| Cluster-internal access | `Service` (`:9099`) |
| External access | `Route` (OpenShift, edge TLS) or `Ingress` |
| Non-secret config (`NANNY_*`, integration URLs) | `ConfigMap` (envFrom) |
| Secrets (auth, webhook, API tokens) | `Secret` or `existingSecret` (envFrom) |
| HA Postgres + the `…-app` secret nanny reads as `DATABASE_URL` | CNPG `Cluster` |
| Optional CPU autoscaling | `HorizontalPodAutoscaler` |

`readyz` returns 503 until `Store.healthy()` (a Postgres `SELECT 1`) succeeds, so
pods only take traffic once the database is reachable. The schema is applied
idempotently by nanny at startup (`db/schema.sql`) — no migration Job needed.

## Common values

| Key | Default | Notes |
|---|---|---|
| `replicaCount` | `2` | Stateless; raise freely or use `autoscaling.enabled`. |
| `image.repository` / `image.tag` | `nanny` / appVersion | Point at your registry. |
| `auth.mode` | `password` | `ldap` \| `password` \| `open` (open needs `auth.insecure=true`). |
| `webhookToken` | `""` | Set it — unset leaves `/alert` + `/icinga` unauthenticated. |
| `llm.backend` | `none` | `anthropic` \| `openai` \| `none` (deterministic triage). |
| `postgres.enabled` | `true` | `false` → set `databaseUrlSecret.name` to an external PG secret. |
| `postgres.instances` | `3` | CNPG HA (1 primary + 2 replicas). |
| `route.enabled` | `true` | OpenShift Route; disable on vanilla k8s. |

## Validate locally

```sh
helm lint  ./deploy/helm/nanny --set auth.password=x --set webhookToken=y
helm template rel ./deploy/helm/nanny --set auth.password=x --set webhookToken=y
```

> Multi-replica HA requires `postgres.enabled=true` (or an external `DATABASE_URL`).
> With both off, nanny falls back to the in-memory store — fine for one pod, but
> state is lost on restart and **not** shared across replicas.
