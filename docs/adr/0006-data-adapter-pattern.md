# ADR-0006 — Data adapter pattern: broker-specific code stays behind the adapter

**Status:** Accepted (M2)
**Date:** 2026-05-25

## Context

We need to support many data sources: Alpaca (paper / live), Interactive Brokers,
TD Ameritrade/Schwab, Tradier, crypto exchanges via CCXT, plus a fixture replay
adapter for tests and backtests. Each has its own auth, pagination, rate limits,
and payload shapes.

## Decision

Every adapter implements one ABC: :class:`data.base.DataAdapter`. The interface is
deliberately narrow — `connect`, `close`, `historical_bars`, `stream_ticks`,
`stream_bars`. Implementations:

- Normalize broker payloads into ``core.schemas.Tick`` / ``Bar`` before yielding.
- Hide pagination, rate limiting, retries, and reconnect entirely.
- Load credentials from a ``SecretProvider`` injected at construction.

Heavy adapters (Alpaca, IBKR) are registered via PEP 562 ``__getattr__`` in
``data/adapters/__init__.py`` so importing the package doesn't drag in
``httpx``/``websockets`` unless the adapter is actually used.

## Consequences

- Strategies, the backtester, and the writer service all consume the same Tick/Bar
  schema regardless of source. Swapping Alpaca for IBKR is a config change.
- A new adapter is ~200 lines (Alpaca is ~250 with WS + REST).
- Tests use ``FixtureAdapter`` for full determinism — no recorded HTTP cassettes,
  just JSONL records of normalized ticks/bars.

## Out of scope (deferred)

- Order routing adapters (different ABC). See M7 / ADR-0010 (forthcoming).
- Multi-broker aggregation/routing for best price. M8+.
