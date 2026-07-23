# ADR-0007 — Indicators compute in float internally; Decimal only at boundaries

**Status:** Accepted (M3)
**Date:** 2026-05-25

## Context

Bar prices flow through the system as `Decimal` so order-fill comparisons and
P&L accounting stay precise. Indicators do a lot of arithmetic per update —
exponentials, square roots, divisions — and `Decimal` is roughly 20-50× slower
than `float` for those.

## Decision

Indicators consume `Bar` objects (with `Decimal` fields), convert to `float` on
the way in, and emit readings whose `values` are `float`. Strategies that need to
compare an indicator output to a price convert the `float` back to `Decimal` at
the boundary (e.g., `Decimal(str(reading.value()))`).

Trade-off: indicator values are mathematically meaningful but not bit-exact with
recomputation from `Decimal` inputs. That's acceptable because indicators are
inputs to *decisions*, not to *bookkeeping*. The kill-switch, position sizing,
and fill matching all keep `Decimal`.

## Consequences

- Indicator throughput is in the 0.9-3.1 µs/bar range — fast enough to run
  hundreds of indicators on hundreds of instruments at 1Hz without breaking a
  sweat.
- Rust / PyO3 hot path is deferred: profiling shows no need.
- Decimal arithmetic remains the rule on the order/fill/position side; mixing
  is explicit at named conversion points.

## Out of scope

- Vectorized batch compute (numpy/pandas) — usable in research notebooks but the
  live path stays single-bar-at-a-time for streaming.
