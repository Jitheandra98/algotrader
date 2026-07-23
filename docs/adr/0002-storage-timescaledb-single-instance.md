# ADR-0002 — Storage: single TimescaleDB instance for time-series + metadata

**Status:** Accepted (M1)
**Date:** 2026-05-25

## Context

We have two distinct data shapes:

- **Time-series**: ticks, bars, fills, KPI snapshots — high-volume, append-only,
  range-scanned by time.
- **Relational metadata**: instruments, orders, strategy_config, risk_decisions —
  modest volume, point lookups, joins.

We could run Postgres + InfluxDB, Postgres + ClickHouse, or just Postgres with the
TimescaleDB extension (which gives us hypertables in the same database).

## Decision

Use **one TimescaleDB instance**. Hypertables for time-series tables, regular tables
for everything else. Same connection, same transactions, same SQL.

## Consequences

- One binary to operate. Simpler backups, simpler PITR, no cross-store consistency
  questions.
- TimescaleDB scales to ~10–100M rows/day per node comfortably; if we outgrow it,
  we move ticks to a dedicated time-series store and keep everything else in
  Postgres. The repository layer would absorb that change.
- Retention is per-hypertable via TimescaleDB retention policies (configured in a
  later migration).
