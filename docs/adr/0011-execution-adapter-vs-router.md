# ADR-0011 — Broker adapter vs. order router are separate layers

**Status:** Accepted (M7)
**Date:** 2026-05-25

## Context

The brief calls for "a broker-agnostic execution layer with an adapter pattern"
*and* "a smart order router that scales in/out, slices large orders, and
minimizes slippage by checking spread and depth from Level 2 data before
sending." These are two different concerns:

  - **Broker adapter** (`BrokerAdapter`) hides broker-specific API details.
    Code that consumes it doesn't care whether it's talking to Alpaca, IBKR,
    or a paper simulator.
  - **Order router** (`SmartOrderRouter`) decides *how* to send an order —
    slicing, downgrading market → limit on wide spreads, depth-aware sizing.
    It calls `broker.submit()` for each child it produces.

If we collapse these into one class, every broker adapter would need its own
router logic and we'd duplicate the slicer / spread guard code N times.

## Decision

Two layers:

  1. `BrokerAdapter` is the *thin* protocol — submit / cancel / get / stream.
     One concrete implementation per broker.
  2. `SmartOrderRouter` wraps a `BrokerAdapter`. It owns the `MarketView`,
     slicers, spread/depth guards. The `LiveEngine` always calls the router,
     not the broker directly.

For paths that don't need smarts (backtest), the router is bypassed —
`backtest.broker.SimulatedBroker` is consumed directly by `BacktestEngine`.
The router lives in `execution/` because that's where order intelligence lives
in production.

The router has a sensible default fallback: with no slicer configured and no
quote available, it just passes the order through. So opting in to live
trading with router-default = router as a thin shim isn't worse than the raw
adapter.

## Consequences

- Adding a new broker is ~200 lines of adapter code; the router carries over
  unchanged.
- Adding a new slicer or routing rule is a single file in `execution/`; all
  brokers benefit.
- The market view is *shared state* the router reads. The data adapter
  populates it as ticks arrive. This is the one explicit cross-layer
  dependency in M7.
- Backtest doesn't go through the router. This is a deliberate gap — backtests
  have bars not L2, so most router decisions are no-ops. A future
  "tick-replay backtest" (M9) will route through it for parity.

## Out of scope

- **Multi-broker routing** (e.g., split a single order across brokers for
  best price). Future work; the ABC supports it but no router shipped does it.
- **Order book reconstruction** for advanced routing. M9 / external adapters.
- **Hardware-accelerated paths** (FPGA / kernel-bypass) — entirely out of
  scope for this codebase.
