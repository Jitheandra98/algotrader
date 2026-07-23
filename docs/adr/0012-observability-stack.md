# ADR-0012 — Observability stack: Prometheus + FastAPI panel + multi-channel alerts

**Status:** Accepted (M8)
**Date:** 2026-05-25

## Context

The brief calls for a real-time dashboard, multi-channel alerts, health checks,
and automatic circuit breakers. We need a stack that's standard enough that
ops people don't need bot-specific knowledge to debug a fire at 3 a.m.

## Decision

- **Metrics**: Prometheus `prometheus_client`. Counters / gauges / histograms
  exposed at `/metrics`. Standard naming `algotrader_<noun>_<unit>`.
- **Dashboards**: Grafana, dashboards committed as JSON in
  `monitoring/dashboards/`. Three views: overview, per-strategy, risk.
- **Operator UI**: FastAPI on the same process exposing REST + WebSocket plus
  a single-file dark-mode HTML page. Minimal — no React build chain. Vanilla
  JS polls `/api/*` every 2s. Operators who need richer interactions hit the
  Grafana dashboards.
- **Alerts**: Channel ABC + concrete Slack / Discord (webhook), Email (SMTP
  via `asyncio.to_thread`), Twilio (REST). Dispatcher applies rules by
  severity / tag with channel-level isolation (one failure ≠ all fail) and
  dedup by title.
- **Health + breakers**: pull-model checks (polled by a watchdog), feeding
  circuit breakers that trip the existing `KillSwitch` (M6). Breakers
  themselves don't bypass the risk gate — they go through it.

## Consequences

- One process can run the bot + the operator UI + the metrics endpoint.
  Operators have no extra moving parts to deploy.
- The bot can be deeply broken and the UI still loads — every endpoint
  degrades to "n/a" if its provider isn't wired.
- Alerts are best-effort: a Slack outage doesn't take the bot down. Channel
  failures are logged but never raise.
- Grafana dashboards are version-controlled JSON; promotion via standard
  Grafana provisioning.
- The kill-switch remains the single state machine for "halt new entries."
  Circuit breakers don't have their own gate.

## Out of scope (deferred)

- Distributed tracing (OpenTelemetry spans across strategy → risk →
  broker). M9/M10.
- Persistent audit log to TimescaleDB (M6 risk-audit is in-memory ring buffer
  today). M10 with the schema migration.
- Operator UI as a full React app (multi-page, charting, replay). The minimal
  HTML page covers day-to-day ops; Grafana covers visualization.
