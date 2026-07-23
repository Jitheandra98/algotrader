# `execution/` — Live execution layer

Broker-agnostic order routing, smart slicing, and the async runtime that drives
live (or live-paper) trading. Same strategy / risk / indicator code as the
backtester — only the broker and the orchestration change.

## Architecture

```
Bar/Tick                  Signal              Order
  │                          │                  │
  ▼                          ▼                  ▼
┌────────────────────────────────────────────────────────────┐
│                       LiveEngine                            │
│                                                             │
│   data adapter ──► strategy ──► risk ──► smart router ──►   │
│                                                ▲            │
│                                            slicer           │
│                                                ▲            │
│   broker ◄──── ExecutionReport ◄── trade_updates WS         │
│      │                                                      │
│      └──► portfolio.apply_fill + quality.on_fill            │
└────────────────────────────────────────────────────────────┘
```

## Components

| File                       | Role |
|----------------------------|------|
| `base.py`                  | `BrokerAdapter` ABC, `ExecutionReport`, `AccountInfo` |
| `brokers/alpaca_broker.py` | Alpaca paper/live (REST + WS trade_updates) |
| `router.py`                | `SmartOrderRouter`, `MarketView`, `Quote` |
| `slicer.py`                | `SimpleSplitSlicer`, `TwapSlicer`, `IcebergSlicer` |
| `quality.py`               | `FillQualityTracker` (latency + slippage) |
| `live_engine.py`           | `LiveEngine` async runtime, `LiveContext` |

## Order types supported

All M4 types **plus** broker-side variants where the native broker supports them:

| Type           | Backtest (sim) | Alpaca live |
|----------------|----------------|-------------|
| MARKET         | ✅             | ✅          |
| LIMIT          | ✅             | ✅          |
| STOP           | ✅             | ✅          |
| STOP_LIMIT     | ✅             | ✅          |
| TRAILING_STOP  | ⚠️ basic        | ✅          |
| MOC            | ✅             | ✅ (TIF=cls)|
| LOC            | ✅             | ✅ (TIF=opg)|
| GTC TIF        | ✅             | ✅          |
| IOC TIF        | n/a            | ✅          |
| FOK TIF        | n/a            | ✅          |
| Bracket OCO    | ✅             | ✅ (native order_class) |

## Smart Order Router decisions

1. **Spread guard**: market orders on wide spreads (default > 20 bps) are
   downgraded to limit-at-mid or skipped (per config).
2. **Size threshold**: orders above `slice_above_qty` are sliced.
3. **Depth guard**: orders exceeding `max_pct_of_top × top-of-book size` are
   sliced even if below the size threshold.
4. **Slicer**: TWAP, simple split, or iceberg. Configurable per router.
5. **Pass-through** otherwise.

The router is broker-agnostic — it consumes any `BrokerAdapter`.

## Fill quality tracking

For each fill, the tracker records:
  - Submit → ack latency (ms)
  - Submit → fill latency (ms)
  - Slippage per unit (signed adverse: positive = bad for trader)
  - Reference price (ask for market BUY, bid for market SELL, limit price otherwise)
  - Venue / broker

`stats()` aggregates across configurable (strategy, instrument, time window)
slices. M8 wires this to Grafana.

## Live-trading safety gates

- `AlpacaBroker(live=True)` **refuses to connect** unless `LIVE=true` is set in
  the environment. Both flags must agree — defense in depth.
- All strategy → order paths go through `RiskEngine` (M6). The risk engine
  cannot be bypassed.
- Bracket OCO orders are placed natively when supported (Alpaca's
  `order_class=bracket`). When unsupported, the broker layer falls back to
  separate orders + cancel-sibling-on-fill logic in our code.

## Going from backtest → live (operator runbook)

1. Confirm your strategy works on at least a full year of backtest data with
   the same risk config you intend to use live.
2. Run **paper** first: instantiate `AlpacaBroker(live=False)`. Run for at
   least one week of market hours. Inspect every rejected risk decision.
3. Compare paper fill quality stats vs backtest expected: median latency, % of
   trades within X bps of mid, etc. If paper diverges sharply from backtest,
   investigate before touching real money.
4. Switch to live: `LIVE=true` env + `AlpacaBroker(live=True)` + per-strategy
   `live: true` in config + `gates <strategy>` CLI check.
5. Start with a tiny size cap on `MaxPositionSize`. Scale gradually.

## What's not in M7

- **Reconciler** (live state ↔ broker state drift detection): scaffold present,
  full sync logic deferred to M8 alongside the operator UI.
- **Routing to multiple brokers** (best-price routing): the ABC supports it but
  the router currently uses a single broker.
- **POV / VWAP slicers**: only `SimpleSplitSlicer`, `TwapSlicer`, `IcebergSlicer`
  ship. Add more in M9 with the ML overlay.
