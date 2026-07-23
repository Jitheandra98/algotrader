# Architecture

The bot is a chain of seven layers connected by an event bus. Each layer publishes
to streams the next layer consumes; nothing reaches across layers directly.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  ┌────────────┐   ticks      ┌────────────┐   features    ┌────────────┐    │
│  │  L1 Data   ├─────────────►│ L2 Features├──────────────►│ L3 Strategy│    │
│  │ Ingestion  │   bars       │  Indicators│               │  Engine    │    │
│  └────────────┘              └────────────┘               └─────┬──────┘    │
│                                                                  │ Signal   │
│                                                                  ▼          │
│  ┌────────────┐  Fill      ┌────────────┐  sized Order   ┌────────────┐   │
│  │ L7 Persist │◄───────────│ L5 Execute │◄───────────────│  L4 Risk   │   │
│  │  Postgres  │            │  Routers   │                │   Gate     │   │
│  │ TimescaleDB│            │  Brokers   │                │ (must pass)│   │
│  └─────┬──────┘            └─────┬──────┘                └────────────┘   │
│        │                          │                                          │
│        │ metrics, fills, equity   │                                          │
│        ▼                          ▼                                          │
│            ┌──────────────────────────────────┐                              │
│            │     L6 Monitoring & Alerting     │                              │
│            └──────────────────────────────────┘                              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Boundaries

- **L1 → L2:** ``data.ticks`` and ``data.bars.{tf}`` Redis streams.
- **L2 → L3:** in-process feature cache + ``data.bars`` (strategies subscribe to bars
  and call the feature engine for indicators).
- **L3 → L4:** ``signals`` stream.
- **L4 → L5:** ``orders.intent`` stream (risk-approved, sized orders only).
- **L5 → L7:** ``fills``, ``orders.events`` for persistence.
- **All → L6:** ``alerts`` + Prometheus metrics scraped from each process.

## Paper / live parity

Backtest, paper, and live trading all use the **same** strategy, risk, and execution
modules. The only thing that changes:

| Layer       | Backtest                          | Paper                          | Live                                |
| ----------- | --------------------------------- | ------------------------------ | ----------------------------------- |
| Data        | Historical replay adapter         | Broker live feed (paper acct)  | Broker live feed (real acct)        |
| Execution   | SimulatedBroker                   | Broker paper adapter            | Broker live adapter                  |
| Bus         | InMemoryBus                       | Redis                          | Redis                                |
| Persistence | Postgres (separate DB or schema) | Postgres                       | Postgres                             |

## Live-trading gating

Three independent flags must all be true before a real-money trade is sent:

1. `LIVE=true` global env var (operator action — kill switch).
2. `broker.live = true` in config (deployment-level — paper vs live broker URL).
3. `strategies[i].live = true` per-strategy (audit-level — only audited strategies
   ever flip this).

`Settings.is_live_strategy(name)` returns the AND of these. Risk and execution both
call it; either layer refusing causes paper routing.

## Key cross-cutting concerns

- **Money:** all monetary fields are `Decimal`. Float math is forbidden in the money
  path (ADR-0003).
- **Time:** all timestamps are UTC and timezone-aware. Schemas reject naive datetimes.
- **Idempotency:** consumers must dedup via `client_order_id` or `signal.id`. Bus
  delivery is at-least-once.
- **Logging:** structlog with redaction. No `print`. No bare `except`.
- **Tests:** unit tests run with no docker. Integration tests need Redis + Postgres,
  marked with `integration` and run separately in CI.

See `docs/adr/` for individual decisions.
