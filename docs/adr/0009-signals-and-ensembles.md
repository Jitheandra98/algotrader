# ADR-0009 — Signals are decoupled from orders; ensemble lives in a filter chain

**Status:** Accepted (M5)
**Date:** 2026-05-25

## Context

The brief separates *signals* (strategy intent) from *orders* (sized trade
requests). Risk management must sit between strategies and orders so it cannot
be bypassed. Multi-strategy systems also need a way for several strategies to
contribute to a decision without each one placing its own trades and creating
chaos.

## Decision

- Strategies emit :class:`Signal` objects via the context. They never call the
  broker directly.
- A configurable chain of :class:`SignalFilter` runs between strategy emission
  and the risk → order conversion. Filters are first-class objects with state
  when needed (windowed confirmation, conviction thresholds, opposing-signal
  cancellation). The chain is applied in order.
- The ensemble layer is one of these filters — :class:`ConfirmationFilter` —
  not a special path. Adding new combination rules is "write a `SignalFilter`."
- :class:`StrategyManager` implements :class:`Strategy` so multi-strategy runs
  look identical to single-strategy runs from the engine's POV.
- Indicators are shared across strategies: ``ctx.request_indicator`` returns the
  same key if (instrument, name, params) match an existing registration. RSI(14)
  on SPY is computed once even if five strategies use it.

## Consequences

- The signal → filter → risk → order pipeline is observable end-to-end. Every
  signal that gets filtered out can be logged with the reason.
- New ensemble rules (weighted voting, ML-rerank, etc.) plug in without
  touching the engine.
- Strategies are testable in isolation — they emit signals through a stub
  context.
- The covered-call stub raises on construction rather than no-oping silently,
  matching the brief's "fail loud near the unsafe path" guardrail.

## Out of scope

- ML signal-quality filter (M9) — will be another `SignalFilter`.
- Performance-weighted ensemble (track each strategy's recent P&L; weight votes
  by it) — also M9.
- Cross-asset signal aggregation (e.g., one strategy's LONG signal on SPY
  validates another's LONG signal on tech sector stocks) — left to consumer-level
  filter combinations.
