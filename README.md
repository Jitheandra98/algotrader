# algotrader

Event-driven, multi-asset, multi-strategy trading bot. Built for **paper/live parity** —
backtests execute the *same* code paths as live trading. Risk management sits between
strategies and execution and is never bypassable.

> ⚠️  **Defaults to paper.** Real-money trading requires `LIVE=true` in the environment
> *and* `live: true` on the specific strategy's config. The kill-switch is hard-coded
> and there is no flag to disable it.

## Status

| Milestone | Description | Status |
|-----------|-------------|--------|
| M1 | Foundation (schemas, bus, config, persistence, secrets, CI) | ✅ Done |
| M2 | Data ingestion (Alpaca, aggregators, writers, ingestion pipeline) | ✅ Done |
| M3 | Feature engine (48 indicators + patterns + relative strength) | ✅ Done |
| M4 | Backtesting engine + KPIs + Monte Carlo + tear sheet | ✅ Done |
| M5 | Strategy engine + 8 reference strategies + ensemble filters | ✅ Done |
| M6 | Risk management (sizing + 13 limits + PDT/SSR + Greeks + kill-switch) | ✅ Done |
| M7 | Execution (Alpaca live broker + smart router + slicers + fill quality) | ✅ Done |
| M8 | Monitoring + alerts + operator UI (Prometheus + Grafana + FastAPI) | ✅ Done |
| M9 | ML overlay (sklearn signal-quality, walk-forward CV) + trade journal + research workspace | ✅ Done |
| M10 | Production hardening (K8s + DR + runbooks + chaos tests + security review) | ✅ Done |

## Quick start

```bash
# 1. Install
poetry install --with dev

# 2. Spin up local services (Redis + TimescaleDB)
make up

# 3. Apply DB migrations
make migrate

# 4. Run tests
make test

# 5. Tear down
make down
```

## Architecture

Seven layers, connected by an event bus:

```
   Layer 1: Data Ingestion       (broker adapters, normalization, aggregators)
        │ ticks / bars / events
        ▼
   Layer 2: Feature Engineering  (indicators, patterns, relative strength)
        │ features
        ▼
   Layer 3: Strategy Engine      (registry, ensemble, ML overlay)
        │ Signal
        ▼
   Layer 4: Risk Management      ◄── gate; no signal becomes an order without passing
        │ sized Order
        ▼
   Layer 5: Execution            (broker-agnostic; smart router; bracket OCO)
        │ Fill
        ▼
   Layer 6: Monitoring           (metrics, dashboards, alerts, circuit breakers)
        │
        ▼
   Layer 7: Persistence          (TimescaleDB ticks/bars/fills; Postgres metadata)
```

See [docs/architecture.md](docs/architecture.md) for detail; ADRs live in
[docs/adr/](docs/adr/).

## Layout

```
core/         schemas, config, logging, event bus, secrets
data/         broker adapters, ingestion, tick aggregators
features/    indicators, patterns, relative strength
strategies/  strategy base class + plugin registry + reference strategies
risk/         sizing, limits, Greeks, PDT/SSR enforcement
execution/   order router, broker adapters, simulated broker
monitoring/  metrics, alerts, dashboards
persistence/ DB schemas, migrations, repositories
backtest/    event-driven backtester, KPIs, walk-forward, Monte Carlo
ml/           feature store, signal-quality model, walk-forward trainer, MLSignalFilter
journal/      per-trade rationale capture (JSONL + DB)
research/     Jupyter notebooks + helpers
config/       YAML configs per environment
tests/        unit, chaos, integration
docs/         architecture, runbooks, ADRs, API reference, security review
deploy/       Dockerfiles, docker-compose, K8s manifests, DR scripts
```

## Coding standards

- Strict type hints (mypy strict mode, no errors in CI).
- Pydantic v2 for all external boundaries; `attrs` for internal value objects.
- `Decimal` for all monetary values; never `float`.
- Structured logging via `structlog` — no `print`, no bare `except`.
- Tests target ≥80% coverage on `core`, `risk`, `execution`; ≥60% elsewhere.

## Safety

- Risk management is the **only** path between signals and orders. Strategies cannot
  emit orders directly.
- The kill-switch on consecutive losses and drawdown is hard-wired; there is no
  configuration that disables it.
- Secrets are loaded from environment / vault at runtime and redacted in logs.
- No real-money trade is placed without both `LIVE=true` (env) and the strategy's
  per-strategy `live: true` flag (config).
