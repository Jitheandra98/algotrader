# ADR-0003 — All monetary values use `Decimal`, never `float`

**Status:** Accepted (M1)
**Date:** 2026-05-25

## Context

`float` representation of monetary values introduces silent rounding errors. For a
trading bot these errors compound across thousands of fills and reconciliations and
will eventually mis-match the broker's books.

## Decision

Every money- or quantity-shaped field in `core/schemas.py` is typed as `Decimal`.
The DB column type is `NUMERIC(20,8)`. Computations involving money use `Decimal`
arithmetic.

Indicator math and statistical features may use `float`/`numpy.float64` because the
error tolerance is acceptable there and the math libs assume float — but the
**output** of any computation that feeds into a price or quantity must be converted
to `Decimal` at the boundary, with explicit rounding mode.

## Consequences

- Pydantic schemas reject float inputs for money fields.
- A linter rule will be added (M3) to flag `float()` casts inside `risk/` and
  `execution/`.
- Performance is fine: Pydantic v2 + Decimal handles single-tick latency well below
  our latency budget.

## Alternatives considered

- **Fixed-point integers (cents)** — works for USD equities but breaks for crypto
  (8 decimals) and forex (5 decimals). `Decimal` handles both uniformly.
