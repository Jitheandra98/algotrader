# `features/` — Indicator & pattern library

48 stateful indicators and pattern detectors behind a uniform interface, plus a
cross-instrument relative-strength calculator. Strategies (M5) consume these.

## Quick start

```python
from features import create

rsi = create("rsi", period=14)
for bar in bars:
    reading = rsi.update(bar)
    if reading.ready:
        print(reading.values["value"])  # 0..100
```

Multi-output indicators (MACD, Bollinger, etc.) expose multiple fields on the same
reading:

```python
macd = create("macd", fast=12, slow=26, signal=9)
r = macd.update(bar)
r.values  # {"macd": ..., "signal": ..., "hist": ...}
```

## Registered indicators (48)

**Moving averages** (9):  `sma  ema  dema  tema  wma  hma  alma  lsma  vwma`

**Oscillators** (7):  `rsi  macd  stoch  willr  cci  vortex  trix`

**Bands** (4):  `bbands  keltner  donchian  envelope`

**Volume** (6):  `vwap  obv  adline  rvol  vbp` (+ anchored VWAP via `VWAP(anchor=ts)`)

**Directional / stops** (6):  `adx  atr  supertrend  psar  chandelier  volstop`

**Regression / counting** (3):  `linreg  tdseq  strat`

**Candlestick patterns** (8):  `pattern.doji  pattern.hammer  pattern.inverted_hammer
pattern.engulfing_bull  pattern.engulfing_bear  pattern.three_white_soldiers
pattern.three_black_crows  pattern.mat_hold  pattern.stick_sandwich`

**Chart patterns** (5):  `pattern.head_and_shoulders  pattern.inverse_head_and_shoulders
pattern.double_top  pattern.double_bottom  pattern.vcp`

**Cross-instrument** (not in registry):  `RelativeStrength` (benchmark or rank mode)

## Performance (50k bars, dev macOS)

| Indicator   | µs/bar  | Bars/s     |
|-------------|---------|------------|
| SMA         | 1.11    | 900k       |
| EMA         | 0.95    | 1.05M      |
| RSI         | 1.31    | 760k       |
| MACD        | 2.74    | 365k       |
| Bollinger   | 3.07    | 326k       |
| ATR         | 1.25    | 800k       |
| ADX         | 1.78    | 561k       |
| SuperTrend  | 2.72    | 367k       |
| VWAP        | 1.46    | 683k       |
| OBV         | 0.99    | 1.01M      |

Rust hot path is not warranted at this stage — the slowest indicator (Bollinger,
3 µs/bar) is more than fast enough for 1m bars × 1000 instruments × 100 indicators
in under 0.5 second.

## Adding a new indicator

```python
from features.base import Indicator
from features.registry import register
from features.series import RollingSeries

class MyMA(Indicator):
    name = "myma"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        self._series = RollingSeries(period)
        self.warmup = period

    def _update(self, bar) -> dict[str, float] | None:
        self._series.push(float(bar.close))
        return {"value": self._series.mean()}

@register("myma")
def _make(period: int = 20) -> Indicator:
    return MyMA(period=period)
```

Import the file from `features/indicators/__init__.py` so it registers on package
load, then it's available via `create("myma", period=20)`.

## Internal precision

All indicators use `float` internally for speed. Bar prices come in as `Decimal`
(from `core.schemas.Bar`) and are converted on input. Strategies that compare
indicator output to actual prices should convert the indicator's `float` back to
`Decimal` at the boundary.
