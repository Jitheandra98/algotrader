# `risk/` — Risk Management

The risk engine sits between strategy signal emission and broker order
submission. **No strategy can bypass it.** Per the brief and ADR-0010:

> "Risk management must sit between strategies and order execution — no strategy
>  can bypass it."

## Pipeline

```
Signal ─►─ Sizer ─►─ Order draft ─►─ Limit chain ─►─ RiskDecision
                                       │
                                       ├─ PerTradeRiskCap
                                       ├─ MaxPositionSize
                                       ├─ PerStrategyAllocation
                                       ├─ GrossLeverage / NetLeverage
                                       ├─ DailyDrawdownLimit / WeeklyDrawdownLimit
                                       ├─ KillSwitch / ConsecutiveLossKill
                                       ├─ PatternDayTrader
                                       ├─ ShortSaleRestriction
                                       ├─ SectorExposureLimit
                                       ├─ CorrelationLimit
                                       ├─ AggregateGreeksBound
                                       └─ WashSaleGuard
```

Every decision — allowed or blocked — is recorded in the engine's audit log with
the full rationale.

## Public surface

```python
from risk import (
    RiskEngine,
    # Sizers
    FixedFractionalSizer, VolTargetedSizer, KellySizer,
    # Limits
    PerTradeRiskCap, MaxPositionSize, PerStrategyAllocation,
    GrossLeverage, NetLeverage,
    DailyDrawdownLimit, WeeklyDrawdownLimit,
    KillSwitch, ConsecutiveLossKill,
    PatternDayTrader, ShortSaleRestriction,
    SectorExposureLimit, CorrelationLimit,
    AggregateGreeksBound,
    WashSaleGuard, LotSelector, LotMethod, TaxLot,
)
```

## Backtest example

```python
risk = RiskEngine(
    sizer=FixedFractionalSizer(risk_per_trade_pct="0.005"),  # brief's 0.5% default
    limits=[
        PerTradeRiskCap(max_pct="0.01"),
        MaxPositionSize(max_pct="0.20"),
        GrossLeverage(max_leverage="1.0"),
        DailyDrawdownLimit(max_pct="0.03"),
        WeeklyDrawdownLimit(max_pct="0.07"),
        ConsecutiveLossKill(max_consecutive=5),
        KillSwitch(),
        PatternDayTrader(),
        ShortSaleRestriction(),
        SectorExposureLimit({"TECH": "0.30"}),
        CorrelationLimit(max_cluster_pct="0.40", rho_threshold=0.6),
        AggregateGreeksBound(max_abs_delta=Decimal("100")),
        WashSaleGuard(block=False),
    ],
)
engine = BacktestEngine(strategy=..., broker=..., portfolio=..., risk_engine=risk)
```

## Sizers

| Sizer                    | Inputs                  | Output |
|--------------------------|-------------------------|--------|
| `FixedFractionalSizer`   | risk%, target alloc%    | qty = (risk × equity) / |entry − stop| (stop-based) or (alloc × equity) / price (fallback) |
| `VolTargetedSizer`       | risk%, k×ATR multiplier | qty = (risk × equity) / (k × ATR), with fallback to alloc when ATR not warm |
| `KellySizer`             | half-Kelly default      | f* = win_rate − (1−win_rate)/payoff, capped at max_pct, scaled by ``kelly_fraction`` |

All sizers scale by `signal.conviction ∈ [0, 1]`.

## Limits

| Limit                       | What it checks                                                |
|-----------------------------|---------------------------------------------------------------|
| `PerTradeRiskCap`           | $-at-risk per trade ≤ pct of equity                           |
| `MaxPositionSize`           | Total notional in instrument ≤ pct of equity                  |
| `PerStrategyAllocation`     | Per-strategy total notional ≤ pct of equity                   |
| `GrossLeverage`             | Σ |position values| / equity ≤ cap                            |
| `NetLeverage`               | Signed net / equity ∈ [−short_cap, +long_cap]                 |
| `DailyDrawdownLimit`        | (open − now) / open ≤ pct during session                      |
| `WeeklyDrawdownLimit`       | (Mon open − now) / Mon open ≤ pct                             |
| `KillSwitch`                | Manual full-stop with audit reasons                           |
| `ConsecutiveLossKill`       | Trip after N consecutive losses; optional auto-cooldown       |
| `PatternDayTrader`          | <$25K margin + 4th day-trade in 5 business days → block       |
| `ShortSaleRestriction`      | Coarse uptick proxy when SSR flagged                          |
| `SectorExposureLimit`       | Per-sector notional ≤ pct of equity                           |
| `CorrelationLimit`          | |ρ|-weighted exposure cluster ≤ pct of equity                 |
| `AggregateGreeksBound`      | Portfolio |delta|/|gamma|/|vega| ≤ caps                       |
| `WashSaleGuard`             | Flag (default) or block opening BUY within 30 days of a loss  |

## Soundness property (tested)

`tests/unit/test_risk_engine.py::test_property_allowed_trade_passes_all_limits`
runs a property test over 81 combinations of (equity, risk%, stop distance,
leverage cap): for every allowed decision, no limit's `check()` returns a
rejection on the same input. This is the invariant the brief demands.

## Audit log

Every decision is appended to `engine.audit_log`. For blocked decisions, all
rejections are preserved (multiple limits may have caught the same trade — the
log tells you which). Structured `risk_rejection` log lines are emitted at WARN
level with the rejecting limit names and signal id.

## Adding a new limit

1. Subclass `RiskLimit`, implement `check()` returning `RiskRejection | None`.
2. Override `update_on_fill()` if the limit is stateful.
3. Register in `risk/limits/__init__.py`.
4. Add to your `RiskEngine(limits=[...])` config.

## What's out of scope (deferred)

- **Live Greeks** require an option-chain data adapter (M2/M7). The framework
  is complete; only the data feed is missing.
- **Real-time wash-sale ledger** persisted to the DB (M9 — tax journal).
- **Pre-trade margin call simulation** for margin accounts. M7.
- **Cross-account aggregation** for multi-account operators. M8+.
