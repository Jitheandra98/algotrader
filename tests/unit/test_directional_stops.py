"""ADX / ATR / SuperTrend / PSAR / Chandelier / VolStop tests."""

from __future__ import annotations

import math

import pytest

from features import create
from tests.unit._indicator_helpers import bars_from_closes, bars_from_ohlcv


def test_atr_constant_range_is_constant() -> None:
    atr = create("atr", period=3)
    # All bars: H-L = 2. ATR = 2.
    bars = bars_from_ohlcv([(10, 11, 9, 10, 100) for _ in range(10)])
    readings = [atr.update(b) for b in bars]
    assert readings[-1].ready
    assert readings[-1].values["value"] == pytest.approx(2.0)


def test_adx_uptrend_di_dominates() -> None:
    adx = create("adx", period=5)
    bars = bars_from_ohlcv([(10 + i, 10 + i + 1, 10 + i - 0.5, 10 + i + 0.8, 100) for i in range(30)])
    readings = [adx.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["plus_di"] > final.values["minus_di"]
    assert 0 <= final.values["adx"] <= 100


def test_supertrend_uptrend_returns_lower_band_with_trend_one() -> None:
    st = create("supertrend", period=5, factor=3.0)
    bars = bars_from_ohlcv([(10 + i * 0.5, 10 + i * 0.5 + 0.5, 10 + i * 0.5 - 0.5, 10 + i * 0.5 + 0.3, 100) for i in range(20)])
    readings = [st.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["trend"] == 1.0
    # Lower band tracks below price in an uptrend.
    assert final.values["value"] < float(bars[-1].close)


def test_psar_initializes_then_tracks() -> None:
    psar = create("psar")
    bars = bars_from_ohlcv([(10, 11, 9.5, 10.5, 100), (10.5, 12, 10, 11.5, 100), (11.5, 12.5, 11, 12, 100)])
    readings = [psar.update(b) for b in bars]
    # By bar 2 we should have a trend.
    assert readings[-1].values["trend"] in (1.0, -1.0)


def test_chandelier_long_exit_below_high() -> None:
    chand = create("chandelier", period=5, atr_period=5, factor=3.0)
    bars = bars_from_ohlcv([(10 + i, 10 + i + 1, 10 + i - 0.5, 10 + i + 0.5, 100) for i in range(15)])
    readings = [chand.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["long_exit"] < max(float(b.high) for b in bars[-5:])


def test_volstop_initial_trend_long() -> None:
    vs = create("volstop", atr_period=3, factor=2.0)
    bars = bars_from_ohlcv([(10, 11, 9, 10, 100) for _ in range(10)])
    readings = [vs.update(b) for b in bars]
    assert readings[-1].ready
    assert readings[-1].values["value"] < 10  # stop below price in long trend
