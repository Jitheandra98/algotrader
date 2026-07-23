"""Oscillator tests, including the classic Welles Wilder RSI(14) cross-check."""

from __future__ import annotations

import math

import pytest

from features import create
from tests.unit._indicator_helpers import bars_from_closes, bars_from_ohlcv


WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28,
]
"""Welles Wilder's published RSI(14) example. The expected RSI on the last bar
is documented in many references as ≈ 70.46."""


def test_rsi_wilder_reference() -> None:
    rsi = create("rsi", period=14)
    bars = bars_from_closes(WILDER_CLOSES)
    readings = [rsi.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["value"] == pytest.approx(70.46, abs=0.5)


def test_rsi_all_gains_pegs_100() -> None:
    rsi = create("rsi", period=5)
    bars = bars_from_closes([1, 2, 3, 4, 5, 6, 7, 8])
    readings = [rsi.update(b) for b in bars]
    # Pure uptrend: avg_loss = 0 → RSI = 100.
    assert readings[-1].ready
    assert readings[-1].values["value"] == pytest.approx(100.0)


def test_macd_signal_after_warmup() -> None:
    macd = create("macd", fast=12, slow=26, signal=9)
    bars = bars_from_closes(list(range(1, 60)))
    readings = [macd.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert not math.isnan(final.values["macd"])
    assert not math.isnan(final.values["signal"])
    # Hist = MACD - signal
    assert final.values["hist"] == pytest.approx(
        final.values["macd"] - final.values["signal"], abs=1e-9
    )


def test_stochastic_at_extremes() -> None:
    """Close at high → %K = 100. Close at low → %K = 0."""
    stoch = create("stoch", period=3, smooth_k=1, smooth_d=1)
    # Build bars where each bar's close == high (closes at top of range).
    bars = bars_from_ohlcv([(50, 51, 49, 51, 100)] * 5)
    readings = [stoch.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["k"] == pytest.approx(100.0)

    stoch_down = create("stoch", period=3, smooth_k=1, smooth_d=1)
    bars_dn = bars_from_ohlcv([(50, 51, 49, 49, 100)] * 5)
    readings_dn = [stoch_down.update(b) for b in bars_dn]
    assert readings_dn[-1].values["k"] == pytest.approx(0.0)


def test_willr_range() -> None:
    willr = create("willr", period=3)
    bars = bars_from_ohlcv([(50, 51, 49, 51, 100)] * 5)
    readings = [willr.update(b) for b in bars]
    # Close at high → -100*(51-51)/(51-49) = 0.
    assert readings[-1].values["value"] == pytest.approx(0.0)


def test_cci_smoke() -> None:
    cci = create("cci", period=5)
    bars = bars_from_closes(list(range(1, 20)))
    readings = [cci.update(b) for b in bars]
    final = readings[-1]
    assert final.ready


def test_vortex_smoke() -> None:
    vortex = create("vortex", period=5)
    bars = bars_from_closes(list(range(1, 20)))
    readings = [vortex.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    # Pure uptrend: VI+ should be > VI-.
    assert final.values["vi_plus"] > final.values["vi_minus"]


def test_trix_pct_change() -> None:
    trix = create("trix", period=5)
    bars = bars_from_closes(list(range(1, 30)))
    readings = [trix.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    # On a steady linear uptrend the triple-EMA also grows so TRIX > 0.
    assert final.values["value"] > 0
