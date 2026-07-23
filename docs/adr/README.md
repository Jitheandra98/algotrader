# Architecture Decision Records

Each ADR captures one decision: why we chose it, what we considered, and what the
consequences are.

| #    | Title                                                                                              | Status   |
|------|----------------------------------------------------------------------------------------------------|----------|
| 0001 | [Event bus: Redis Streams (with abstraction for Kafka later)](0001-event-bus-redis-streams.md)     | Accepted |
| 0002 | [Storage: single TimescaleDB instance for time-series + metadata](0002-storage-timescaledb-single-instance.md) | Accepted |
| 0003 | [All monetary values use `Decimal`, never `float`](0003-money-as-decimal.md)                       | Accepted |
| 0004 | [The risk gate is the only path from Signal to Order](0004-risk-gate-not-bypassable.md)            | Accepted |
| 0005 | [Pydantic v2 at boundaries, attrs internally](0005-pydantic-at-boundaries-attrs-internal.md)       | Accepted |
| 0006 | [Data adapter pattern: broker-specific code stays behind the adapter](0006-data-adapter-pattern.md) | Accepted |
| 0007 | [Indicators compute in float internally; Decimal only at boundaries](0007-indicators-float-internal.md) | Accepted |
| 0008 | [Backtest and live share the same code path](0008-backtest-live-parity.md) | Accepted |
| 0009 | [Signals are decoupled from orders; ensemble lives in a filter chain](0009-signals-and-ensembles.md) | Accepted |
| 0010 | [Risk runs on every order path](0010-risk-on-every-path.md)                                       | Accepted |
| 0011 | [Broker adapter vs. order router are separate layers](0011-execution-adapter-vs-router.md)         | Accepted |
| 0012 | [Observability stack: Prometheus + FastAPI panel + multi-channel alerts](0012-observability-stack.md) | Accepted |
| 0013 | [ML overlay uses strict walk-forward CV and fails open](0013-ml-overlay-walkforward.md)            | Accepted |
| 0014 | [Production hardening boundaries](0014-production-hardening.md)                                    | Accepted |

## Process

- Number sequentially. Don't renumber.
- One decision per ADR.
- Title is a noun phrase. Status is one of `Proposed`, `Accepted`, `Superseded by ADR-NNNN`, `Deprecated`.
- Use a new ADR (not an edit) to reverse a decision; mark the old one Superseded.
