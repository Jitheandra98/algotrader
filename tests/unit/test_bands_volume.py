"""Bollinger / Keltner / Donchian / Envelope + Volume indicator tests."""

from __future__ import annotations

import math

import pytest

from features import create
from tests.unit._indicator_helpers import bars_from_closes, bars_from_ohlcv


def test_bollinger_constant_series_zero_width() -> None:
    bb = create("bbands", period=5, k=2.0)
    bars = bars_from_closes([100.0] * 10)
    readings = [bb.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["upper"] == pytest.approx(final.values["lower"])
    assert final.values["width"] == pytest.approx(0.0)


def test_bollinger_known_values() -> None:
    bb = create("bbands", period=4, k=2.0)
    bars = bars_from_closes([2, 4, 4, 4, 5])
    readings = [bb.update(b) for b in bars]
    final = readings[-1]
    # Last 4 closes: [4,4,4,5]. mean=4.25. stdev (pop) = sqrt((0.25)*3/4) = sqrt(0.1875)
    assert final.values["middle"] == pytest.approx(4.25)
    std = math.sqrt((0.25 ** 2 * 3 + 0.75 ** 2) / 4)
    assert final.values["upper"] == pytest.approx(4.25 + 2 * std)
    assert final.values["lower"] == pytest.approx(4.25 - 2 * std)


def test_donchian_highest_lowest() -> None:
    donch = create("donchian", period=3)
    bars = bars_from_ohlcv(
        [
            (10, 12, 9, 11, 100),
            (11, 13, 10, 12, 100),
            (12, 14, 11, 13, 100),
            (13, 13.5, 12, 12.5, 100),
        ]
    )
    readings = [donch.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    # Highest high over last 3: 14; lowest low: 10.
    assert final.values["upper"] == pytest.approx(14)
    assert final.values["lower"] == pytest.approx(10)
    assert final.values["middle"] == pytest.approx(12)


def test_envelope_around_sma() -> None:
    env = create("envelope", period=3, percent=10.0)
    bars = bars_from_closes([100, 100, 100])
    readings = [env.update(b) for b in bars]
    final = readings[-1]
    assert final.values["middle"] == pytest.approx(100)
    assert final.values["upper"] == pytest.approx(110)
    assert final.values["lower"] == pytest.approx(90)


def test_keltner_smoke() -> None:
    kel = create("keltner", period=5, atr_period=5, k=2.0)
    bars = bars_from_ohlcv([(10 + i, 10 + i + 0.5, 10 + i - 0.5, 10 + i, 100) for i in range(20)])
    readings = [kel.update(b) for b in bars]
    final = readings[-1]
    assert final.ready
    assert final.values["upper"] > final.values["middle"] > final.values["lower"]


def test_obv_direction_sensitivity() -> None:
    obv = create("obv")
    bars = bars_from_ohlcv(
        [
            (10, 10, 10, 10, 100),
            (10, 11, 10, 11, 50),   # up: +50
            (11, 11, 10, 10, 70),   # down: -70
            (10, 10, 10, 10, 30),   # flat: 0
        ]
    )
    readings = [obv.update(b) for b in bars]
    assert readings[1].values["value"] == pytest.approx(50)
    assert readings[2].values["value"] == pytest.approx(-20)
    assert readings[3].values["value"] == pytest.approx(-20)


def test_ad_line_money_flow_multiplier() -> None:
    ad = create("adline")
    bars = bars_from_ohlcv(
        [
            (10, 12, 8, 12, 100),   # MFM = ((12-8)-(12-12))/4 = 1.0 → +100
            (12, 14, 10, 10, 50),   # MFM = ((10-10)-(14-10))/4 = -1.0 → -50
        ]
    )
    readings = [ad.update(b) for b in bars]
    assert readings[0].values["value"] == pytest.approx(100)
    assert readings[1].values["value"] == pytest.approx(50)


def test_rvol_relative_to_rolling_mean() -> None:
    rvol = create("rvol", period=3)
    bars = bars_from_ohlcv(
        [
            (10, 11, 9, 10, 100),
            (10, 11, 9, 10, 100),
            (10, 11, 9, 10, 100),
            (10, 11, 9, 10, 200),  # 2× the rolling mean (100 over prior 3)
        ]
    )
    readings = [rvol.update(b) for b in bars]
    assert readings[-1].ready
    assert readings[-1].values["value"] == pytest.approx(2.0)


def test_vwap_anchored() -> None:
    from datetime import datetime, timezone

    from features.indicators.volume import VWAP

    UTC = timezone.utc
    anchor = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    vwap = VWAP(anchor=anchor)
    bars = bars_from_ohlcv(
        [
            (10, 12, 8, 10, 100),  # tp = (12+8+10)/3 = 10
            (10, 14, 8, 12, 200),  # tp = (14+8+12)/3 = 34/3 ≈ 11.333
        ],
        start=anchor,
    )
    readings = [vwap.update(b) for b in bars]
    # After two bars: (10*100 + 11.333*200) / 300 = (1000 + 2266.67)/300 ≈ 10.889
    expected = (10 * 100 + (34 / 3) * 200) / 300
    assert readings[-1].values["value"] == pytest.approx(expected)
