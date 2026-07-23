"""Throughput benchmarks for the hot indicators.

Not part of the regular test run — invoke explicitly::

    python3.11 -m pytest tests/benchmarks --no-header

Targets are guidance, not hard SLAs. They exist so we'll notice if a future change
introduces a 10× regression.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.schemas import AssetClass, Bar, BarTimeframe, Instrument
from features import create

UTC = timezone.utc


def _gen_bars(n: int) -> list[Bar]:
    spy = Instrument(symbol="SPY", asset_class=AssetClass.ETF)
    base = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    out: list[Bar] = []
    prev = 100.0
    for i in range(n):
        c = prev + ((i % 7) - 3) * 0.1
        hi = max(prev, c) + 0.05
        lo = min(prev, c) - 0.05
        out.append(
            Bar(
                instrument=spy,
                timeframe=BarTimeframe.M1,
                timestamp=base + timedelta(minutes=i),
                open=Decimal(str(prev)),
                high=Decimal(str(round(hi, 4))),
                low=Decimal(str(round(lo, 4))),
                close=Decimal(str(round(c, 4))),
                volume=Decimal("1000"),
            )
        )
        prev = c
    return out


@pytest.mark.parametrize(
    ("indicator", "params"),
    [
        ("sma", {"period": 20}),
        ("ema", {"period": 20}),
        ("rsi", {"period": 14}),
        ("macd", {"fast": 12, "slow": 26, "signal": 9}),
        ("bbands", {"period": 20, "k": 2.0}),
        ("atr", {"period": 14}),
        ("adx", {"period": 14}),
        ("supertrend", {"period": 10, "factor": 3.0}),
        ("vwap", {}),
        ("obv", {}),
    ],
)
def test_throughput(indicator: str, params: dict[str, object], benchmark_n: int = 50_000) -> None:
    bars = _gen_bars(benchmark_n)
    ind = create(indicator, **params)
    t0 = time.perf_counter()
    for b in bars:
        ind.update(b)
    elapsed = time.perf_counter() - t0
    per_update_us = elapsed * 1e6 / benchmark_n
    print(
        f"\n{indicator}: {benchmark_n} bars in {elapsed:.3f}s "
        f"({per_update_us:.2f}µs/bar, {benchmark_n / elapsed:.0f} bars/s)"
    )
    # Soft floor: nothing should be slower than 100µs per bar on dev hardware.
    assert per_update_us < 100.0, f"{indicator} too slow: {per_update_us:.1f}µs/bar"
