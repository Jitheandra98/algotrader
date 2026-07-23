# `strategies/` — Strategy engine + reference strategies

Plugin-style strategies, multi-strategy manager, and ensemble/confirmation
filters. Same code path runs in backtest and (M7) live.

## Registered strategies (8)

| Name                       | Status      | Single/Multi-instrument | Notes |
|----------------------------|-------------|--------------------------|-------|
| `sma_crossover`            | Live-ready  | Single                   | M4 reference. Long-only by default. |
| `rsi_mean_reversion`       | Live-ready  | Single                   | RSI dip with trend filter. Optional short side. |
| `vcp_breakout`             | Live-ready  | Single                   | Volume-confirmed VCP breakout, Minervini style. |
| `bollinger_squeeze`        | Live-ready  | Single                   | TTM-style squeeze: BB inside Keltner → release direction. |
| `td_sequential_reversal`   | Live-ready  | Single                   | DeMark setup-9 reversal. |
| `fvg_fill`                 | Live-ready  | Single                   | 3-bar Fair-Value-Gap fill, both directions. |
| `pair_trading`             | Live-ready  | **Two** instruments      | Spread z-score with rolling OLS hedge ratio. |
| `covered_call`             | **Stub**    | Single (+ chain)         | Raises until option chains + Greeks (M2 / M6 / M7) land. |

Discover the list at runtime via `strategies.names()`.

## Usage

```python
from strategies import StrategyConfig, create, StrategyManager, StrategySlot, ConfirmationFilter
from backtest import BacktestEngine, Portfolio, SimulatedBroker

inst = Instrument(symbol="SPY", asset_class=AssetClass.ETF)

children = [
    StrategySlot(create("sma_crossover", StrategyConfig(name="sma", instruments=[inst], parameters={"fast_period": 20, "slow_period": 50}))),
    StrategySlot(create("rsi_mean_reversion", StrategyConfig(name="rsi", instruments=[inst]))),
    StrategySlot(create("bollinger_squeeze", StrategyConfig(name="bb", instruments=[inst]))),
]
manager = StrategyManager(StrategyConfig(name="ensemble", instruments=[]), children=children)

engine = BacktestEngine(
    strategy=manager,
    broker=SimulatedBroker(),
    portfolio=Portfolio(initial_cash="100000"),
    signal_filters=[ConfirmationFilter(min_votes=2, window=timedelta(days=10))],
)
result = engine.run(bars)
```

## Lifecycle

A strategy slot has a lifecycle state — `DISABLED`, `PAPER`, or `LIVE`. Disabled
slots don't see bars or emit signals. `PAPER` vs `LIVE` is a label for the
strategy itself; the live-trading gate is enforced at the broker boundary (see
`core.config.Settings.is_live_strategy`). Switch with
`manager.set_lifecycle("name", Lifecycle.DISABLED)`.

## Adding a strategy

```python
# strategies/my_thing.py
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register

class MyThing(Strategy):
    name = "my_thing"
    version = "1"
    def configure(self, ctx): ...
    def on_bar(self, bar, ctx): ...

@register("my_thing")
def _make(config: StrategyConfig) -> Strategy:
    return MyThing(config)
```

Then add the module to `strategies/__init__.py`'s import list so the
`@register` decorator runs at package load. Tests in
`tests/unit/test_reference_strategies.py` are the smoke pattern to follow.

## Ensemble / confirmation filters

Filters run between signal emission and risk/order conversion:

```python
ConfirmationFilter(min_votes=2, window=timedelta(minutes=5))   # require N agreeing strategies
MinConvictionFilter(threshold=0.7)                              # drop low-conviction
StopOpposingSignalsFilter()                                     # cancel when LONG & SHORT collide
```

Filters are stateful (track votes / windows) and applied in order.

## Notes on the multi-strategy demo (5y SPY, 3 strategies + 2-vote confirmation)

```
Buy-and-hold:        +33.78%
Ensemble (3 strats): +1.21% (7 trades, exposure 43.8%, Sharpe 0.14, max DD -3.49%)
```

GBM noise plus strict confirmation = very few trades. This is the engine doing
what it should: the confirmation filter killed most signals because the three
strategies don't agree often in random data. The 7 trades that did fire were the
ones where ≥2 disjoint signals lined up — which is the whole point.
