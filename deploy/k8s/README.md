# Kubernetes manifests

Deployment scaffolding for production. These are **starter manifests** — you
will need to tune resources, replicas, persistent volume sizes, and ingress
class for your cluster. The intent is to encode the topology and the
hard-coded safety constraints; cluster-specific tuning is yours.

## Topology

```
                    ┌─────────────┐
                    │  Ingress    │  (operator-api.example.com)
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ operator-api│  (FastAPI + UI + /metrics)
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼────┐       ┌─────▼─────┐      ┌─────▼─────┐
   │ingester │       │live-engine│      │ Postgres  │  (StatefulSet)
   │(M2)     │──────►│(M7)       │      │ Timescale │
   └────┬────┘       └─────┬─────┘      └───────────┘
        │                  │
        ▼                  ▼
   ┌────────────────────────┐
   │  Redis Streams (bus)   │  (StatefulSet)
   └────────────────────────┘
```

## Apply order

1. ``namespace.yaml`` — create the namespace.
2. ``secrets.yaml`` — populate from your secret store (NEVER commit a populated
   copy; ``secrets.yaml.example`` is the template).
3. ``configmap.yaml`` — non-secret config (broker URLs, log level, etc).
4. ``postgres.yaml`` / ``redis.yaml`` — StatefulSets + PVCs.
5. Wait for Postgres + Redis to be Ready.
6. ``migrations-job.yaml`` — one-shot Alembic upgrade job.
7. ``ingester.yaml``, ``live-engine.yaml``, ``operator-api.yaml`` — the bot
   processes. Each can scale independently except ``live-engine``, which is
   designed to run as a single replica (positions reconciliation against the
   broker is the single source of truth).
8. ``network-policies.yaml`` — restrict pod-to-pod traffic.

## Live trading guardrails

- The ``live-engine`` Deployment defaults to **paper mode**. To enable real
  money: set ``LIVE=true`` in the secret, AND set the per-strategy
  ``live: true`` in the config, AND the strategy must be in the allowlist
  (see ``configmap.yaml``). Three flags must agree.
- ``replicas: 1`` is **hard-coded** on the live engine. Don't scale beyond 1
  unless you've also wired distributed kill-switch state and broker
  reconciliation (M10+ followup).

## Resource sizing (starter values)

| Component     | CPU req | Memory req | Notes |
|---------------|---------|------------|-------|
| operator-api  | 100m    | 256Mi      | mostly idle |
| ingester      | 500m    | 1Gi        | scales with instrument count |
| live-engine   | 1000m   | 2Gi        | indicator + risk hot path |
| postgres      | 1000m   | 4Gi        | bump if backtesting on the same instance |
| redis         | 250m    | 1Gi        | bus throughput driven |

## Scaling

- **Ingester**: replicas = number of distinct instrument groups (no shared
  state between replicas).
- **Live engine**: keep at 1. Multiple replicas would race on broker order
  state.
- **Operator API**: replicas = 2+ for HA. State is read-only against the bot
  process (it reaches in via in-cluster service URL).

## Monitoring stack

If you run Prometheus / Grafana in-cluster, the ServiceMonitor in
``prometheus.yaml`` discovers ``operator-api`` automatically. The Grafana
dashboards in ``monitoring/dashboards/*.json`` are mounted via configmap.

## Secrets

Required keys (see ``secrets.yaml.example``):

| Key                  | Purpose |
|----------------------|---------|
| `LIVE`               | `"true"` to allow real-money trading |
| `DATABASE_URL`       | Postgres DSN |
| `REDIS_URL`          | Redis URL |
| `ALPACA_API_KEY`     | Alpaca API key |
| `ALPACA_API_SECRET`  | Alpaca API secret |
| `SLACK_WEBHOOK_URL`  | (optional) alerts |
| `DISCORD_WEBHOOK_URL`| (optional) alerts |
| `SMTP_*`             | (optional) email alerts |
| `TWILIO_*`           | (optional) SMS alerts |
