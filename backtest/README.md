# `backtest/` — Event-driven backtester

Same indicators → strategy → risk → execution path as live. Only the data source
(``FixtureAdapter`` or DB reader) and the broker (``SimulatedBroker``) differ.

## Architecture

```
                  ┌─────────────────────────────────────────────────┐
                  │              BacktestEngine                     │
                  │                                                 │
   Bar  ─►─┬──────► broker.match(bar)  ──►─ fills ──►─ portfolio    │
            │                                              │         │
            │      indicators.update(bar)                  │         │
            │                                              │         │
            │      strategy.on_bar(bar, ctx) ──►─ signals  │         │
            │                                              │         │
            │      signal → Risk(M6 stub) → Order ──► broker.submit() │
            │                                              │         │
            │      portfolio.mark_bar(bar)  ───────────────┘         │
            └─────────────────────────────────────────────────────────┘
```

## Public surface

```python
from backtest import (
    BacktestEngine, Portfolio, SimulatedBroker,
    BpsSlippage, PerShareCommission,
    compute_kpis, render_tearsheet,
    monte_carlo_paths, WalkForwardRunner,
)
from strategies import SmaCrossoverStrategy
from strategies.base import StrategyConfig
from backtest.synthetic import default_spy_instrument, synthetic_daily_bars
```

## End-to-end example

```python
inst = default_spy_instrument()
bars = synthetic_daily_bars(inst, start=datetime(2021,1,1,tzinfo=UTC), years=5)

strategy = SmaCrossoverStrategy(StrategyConfig(
    name="sma_xover", instruments=[inst],
    parameters={"fast_period": 20, "slow_period": 50},
))
broker = SimulatedBroker(slippage=BpsSlippage(bps="5"),
                        commission=PerShareCommission(per_share="0.005"))
portfolio = Portfolio(initial_cash="100000")

engine = BacktestEngine(strategy=strategy, broker=broker, portfolio=portfolio)
result = engine.run(bars)

print(result.kpis.cagr_pct, result.kpis.sharpe, result.kpis.max_drawdown_pct)

render_tearsheet(
    title="SMA(20/50) on SPY",
    kpis=result.kpis,
    equity_curve=portfolio.equity_curve,
    trades=result.trades,
    output_path=Path("tearsheet.md"),
)
```

## Match policy (no look-ahead)

Orders submitted while processing bar N fill against bar N+1's OHLC:

| Order type        | Match condition                          | Fill price          |
|-------------------|------------------------------------------|---------------------|
| Market            | always                                   | bar.open + slip     |
| Limit BUY         | bar.low ≤ limit                          | limit (pessimistic) |
| Limit SELL        | bar.high ≥ limit                         | limit               |
| Stop BUY          | bar.high ≥ stop                          | max(stop, open) + slip |
| Stop SELL         | bar.low ≤ stop                           | min(stop, open) - slip |
| Stop-Limit        | stop trigger, then limit on same bar     | limit               |
| MOC / LOC         | always / limit-check on close            | bar.close           |
| Bracket OCO       | parent fills → children activate (OCO)   | per child type      |

## Slippage models

`ZeroSlippage`, `PercentSlippage(pct)`, `BpsSlippage(bps)`, `AtrSlippage(factor, fallback_bps)`.

## Commission models

`ZeroCommission`, `PerShareCommission(per_share, minimum, maximum_pct)`,
`PerTradeCommission(per_trade)`, `PercentCommission(pct)`.

## KPI suite

| Bucket       | Metrics |
|--------------|---------|
| Return       | total_return_pct, cagr_pct |
| Risk-adj.    | sharpe, sortino, calmar, mar |
| Drawdown     | max_dd_pct, avg_dd_pct, max_dd_duration_bars, recovery_factor |
| Trades       | trades, wins, losses, win_rate_pct, profit_factor, expectancy, avg_win, avg_loss, largest_win, largest_loss, avg_holding_bars, max_consecutive_wins, max_consecutive_losses |
| Exposure     | exposure_pct |

Annualization factor is inferred from median bar spacing (daily → 252; 1m → 252·6.5·60).

## Walk-forward and Monte Carlo

- `WalkForwardRunner(is_size, oos_size, step, anchored)` yields (IS, OOS) window pairs.
- `monte_carlo_paths(method, …)` runs trade-shuffle or block-bootstrap simulations.

## What's not in M4

- **Risk layer** is a fixed-fractional stub here. Full risk (M6) replaces
  `_signal_to_order` with proper sizing, sector limits, kill-switch, etc.
- **Multi-strategy engines** — M4 runs one strategy. M5 generalizes.
- **Live order routing** — M7.
