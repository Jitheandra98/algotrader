"""LinearRegression, TD Sequential, TheSTRAT bar classifier tests."""

from __future__ import annotations

import pytest

from features import create
from tests.unit._indicator_helpers import bars_from_closes, bars_from_ohlcv


def test_linreg_perfect_line() -> None:
    linreg = create("linreg", period=10, k_bands=2.0)
    bars = bars_from_closes([2 * i + 5 for i in range(10)])
    readings = [linreg.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["slope"] == pytest.approx(2.0)
    assert final.values["value"] == pytest.approx(2 * 9 + 5)
    assert final.values["r_squared"] == pytest.approx(1.0)


def test_linreg_zero_slope_constant() -> None:
    linreg = create("linreg", period=5, k_bands=2.0)
    bars = bars_from_closes([10.0] * 5)
    readings = [linreg.update(b) for b in bars]
    final = readings[-1]
    assert final.values["slope"] == pytest.approx(0.0)
    assert final.values["value"] == pytest.approx(10.0)


def test_tdseq_buy_setup_progresses() -> None:
    tdseq = create("tdseq")
    # 4 priming bars + 9 down bars where close[i] < close[i-4].
    closes = [10, 10, 10, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    bars = bars_from_closes(closes)
    readings = [tdseq.update(b) for b in bars]
    # On the last bar the buy setup count should equal 9.
    assert readings[-1].values["setup_buy"] == 9.0


def test_strat_bar_classification() -> None:
    strat = create("strat")
    # Bar 1: H=11 L=9
    # Bar 2: H=10 L=9.5 → inside (1)
    # Bar 3: H=12 L=9.5 → 2u (higher high, didn't take out low; not outside because low not < prev_low)
    # Bar 4: H=12.5 L=8 → outside (3) — broke both
    bars = bars_from_ohlcv(
        [
            (10, 11, 9, 10, 100),
            (10, 10, 9.5, 10, 100),
            (10, 12, 9.5, 11, 100),
            (10, 12.5, 8, 9, 100),
        ]
    )
    readings = [strat.update(b) for b in bars]
    assert readings[1].values["code"] == pytest.approx(1.0)  # inside
    assert readings[2].values["code"] == pytest.approx(2.1)  # 2u
    assert readings[3].values["code"] == pytest.approx(3.0)  # outside
