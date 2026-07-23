# ADR-0005 — Pydantic v2 at boundaries, attrs internally

**Status:** Accepted (M1)
**Date:** 2026-05-25

## Context

Pydantic validation is a meaningful CPU cost (still milliseconds per object at v2's
performance) which matters when we're processing thousands of ticks per second. But
we *need* validation at boundaries — broker payloads, IPC, config — to catch
malformed input.

## Decision

- **Boundary types** (anything crossing a process, broker, or config boundary) use
  Pydantic v2: `Tick`, `Bar`, `Signal`, `Order`, `Fill`, `Position`, `Settings`.
- **Internal value objects** (indicator state, sized order math, position cache,
  ring buffers) use `attrs.frozen` or `dataclasses(frozen=True, slots=True)` for
  zero-cost construction.

## Consequences

- Hot paths (tick processing, indicator updates) avoid Pydantic overhead.
- Cold paths (config load, broker round-trips) keep strict validation.
- Conversion between Pydantic and attrs types happens at well-defined points (the
  bus consumer constructs a Pydantic model from JSON; the indicator engine then
  converts to an attrs `IndicatorFrame`).
