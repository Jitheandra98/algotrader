# ADR-0010 — Risk runs on every order path

**Status:** Accepted (M6)
**Date:** 2026-05-25

## Context

The brief is unambiguous: *"Risk management must sit between strategies and order
execution — no strategy can bypass it."* This needs to be enforced in code, not
just in convention. A strategy that wants to call `broker.submit()` directly
shouldn't even have a reference to the broker. A future operator who adds a new
order path shouldn't be able to forget the risk check.

## Decision

- Strategies emit :class:`Signal` only. They have no broker reference and no
  way to construct an :class:`Order`. The :class:`StrategyContext` interface
  exposes only ``emit_signal()`` as an output method.
- The engine — backtest or live — owns the only :class:`RiskEngine` instance
  and the only :class:`SimulatedBroker` / live-broker reference. The single
  signal-to-broker path runs through ``RiskEngine.decide()``.
- :class:`RiskEngine` always produces a :class:`RiskDecision`. If allowed, the
  decision carries the final :class:`Order`. If blocked, ``order is None`` and
  the rejections list explains why.
- The engine logs every decision. Blocked decisions emit structured
  ``risk_rejection`` lines at WARN. Audit log is held in memory in M6; M8
  persists to DB.
- Sizers and limits are pure-data on inputs (the :class:`RiskContext`
  Protocol). They don't reach into the engine or broker. This is what makes
  the property test possible: ``allowed ⇒ every limit also passes``.

## Consequences

- A strategy bug that misorders cancels-vs-entries can't trigger an
  unintended trade — the risk engine sees the signal and decides based on
  current portfolio state, not on what the strategy thinks happened.
- The audit log lets us reconstruct *why* the bot did or didn't trade at any
  bar. Post-mortems get much cheaper.
- Property test (`test_risk_engine.py::test_property_allowed_trade_passes_all_limits`)
  encodes the invariant directly: an allowed decision must satisfy every limit
  re-checked against the same context. Regression of this test is a P0.
- Adding a new limit means writing a `RiskLimit` subclass and listing it in
  the engine config. No engine plumbing changes required.

## Out of scope

- Live re-routing through multiple risk engines (one per account / strategy
  cluster) — that's an operator-policy decision in M8.
- Pre-emptive sizing tweaks based on limit predictions ("if this trade would
  breach X, scale it down to fit") — current design is reject-or-allow. A
  scale-down mode could be added as a sizer wrapper without changing the
  engine contract.
