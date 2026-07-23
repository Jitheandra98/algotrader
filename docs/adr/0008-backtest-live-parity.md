# ADR-0008 — Backtest and live share the same code path

**Status:** Accepted (M4)
**Date:** 2026-05-25

## Context

The brief explicitly demands: *"Backtest code and live code must be the same code,
parameterized by data source."* This is non-negotiable. If backtest math diverges
from live math, the strategy that worked in backtest will fail differently in
production — and you only find out with real money.

## Decision

The components in the data → strategy → risk → execution path are written once
and used in both modes:

- ``Indicator`` from :mod:`features` — same instances, same update() calls.
- ``Strategy`` from :mod:`strategies` — same configure/on_bar/on_fill hooks.
- ``Portfolio`` from :mod:`backtest.portfolio` — same fill math in both modes
  (live runs the same dataclass; the live process re-hydrates from the DB on
  restart).
- ``compute_kpis`` from :mod:`backtest.kpis` — used by the operator dashboard at
  runtime *and* by the backtest tear sheet.

What differs between modes:

- **Data adapter**: ``FixtureAdapter`` / DB reader (backtest) vs. ``AlpacaAdapter``
  / IBKR adapter (live). Both implement ``DataAdapter``.
- **Broker**: ``SimulatedBroker`` (backtest) vs. live broker adapter (M7).
- **Driver**: synchronous bar loop in backtest vs. async bus subscription in live.
  The per-bar logic is identical; only the orchestration differs.

The backtest's :class:`BacktestContext` and live's ``LiveContext`` (M7) implement
the same :class:`StrategyContext` Protocol so the strategy code itself is
unchanged across modes.

## Consequences

- A bug in indicator math or strategy logic surfaces in both modes — but at least
  it's the *same* bug. No "but it worked in backtest" mystery.
- The simulated broker has to behave like a real one: no look-ahead, proper
  slippage/commission, fill-on-next-bar semantics. The cost is realism; the
  benefit is trust.
- The risk layer (M6) sits between strategy and broker in both modes. A backtest
  that tries to skip risk would be giving misleading results — so we don't
  expose a way to do that.

## Out of scope

- Tick-replay backtests (we replay bars in M4; tick-replay is M9 work for
  microstructure-sensitive strategies).
- Multi-strategy / multi-account backtests (M5 generalizes the engine).
