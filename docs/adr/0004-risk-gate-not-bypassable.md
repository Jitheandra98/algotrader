# ADR-0004 — The risk gate is the only path from Signal to Order

**Status:** Accepted (M1; enforced in M6/M7)
**Date:** 2026-05-25

## Context

Real-money trading goes wrong in two ways: a bug bypasses a check, or an operator
disables one "just this once." Both kill accounts.

## Decision

1. Strategies emit `Signal` objects only. They have no method that produces an
   `Order` and no broker client in scope.
2. The only consumer of the `signals` stream that produces `orders.intent` is the
   risk module.
3. The execution module consumes `orders.intent` and submits to brokers. It refuses
   to submit any order whose `metadata.risk_decision_id` does not point to a
   recorded `risk_decisions` row.
4. The kill-switch logic (consecutive losses, daily / weekly drawdown) is
   hard-coded and has no config flag that disables it. A "softer" kill threshold
   is configurable, but the *existence* of the gate is not.

## Enforcement

- M6 risk module: property-based tests assert that no input `Signal` can produce
  an `Order` without a corresponding accepted `risk_decisions` row.
- M7 execution: integration test rejects an `orders.intent` message that lacks a
  valid risk decision id.
- Code review: any PR touching `risk/` or `execution/__init__.py` flagged as
  safety-critical.

## Consequences

- Strategies cannot "fast path" around risk for high-frequency setups. That's
  intentional: if a strategy needs lower-latency risk checks, the risk module gets
  an in-process fast path, not a bypass.
