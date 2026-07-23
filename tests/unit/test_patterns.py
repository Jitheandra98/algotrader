"""Candlestick + chart pattern detector tests."""

from __future__ import annotations

import pytest

from features import create
from tests.unit._indicator_helpers import bars_from_ohlcv


# ---- Candlestick -----


def test_doji_detection() -> None:
    doji = create("pattern.doji", threshold=0.05)
    bars = bars_from_ohlcv(
        [
            (10, 12, 8, 10.0, 100),    # body=0, range=4 → doji
            (10, 12, 8, 11.5, 100),    # body=1.5, range=4 → not doji
        ]
    )
    readings = [doji.update(b) for b in bars]
    assert readings[0].values["signal"] == 1.0
    assert readings[1].values["signal"] == 0.0


def test_bullish_engulfing() -> None:
    eng = create("pattern.engulfing_bull")
    bars = bars_from_ohlcv(
        [
            (12, 12.5, 10, 10.5, 100),  # bearish: open=12, close=10.5
            (10, 13, 9.5, 12.5, 100),   # bullish: open=10 ≤ 10.5, close=12.5 ≥ 12
        ]
    )
    readings = [eng.update(b) for b in bars]
    assert readings[1].values["signal"] == 1.0


def test_bearish_engulfing() -> None:
    eng = create("pattern.engulfing_bear")
    bars = bars_from_ohlcv(
        [
            (10, 12.5, 9.5, 12, 100),    # bullish: open=10, close=12
            (12.5, 13, 9, 9.5, 100),     # bearish: open=12.5 ≥ 12, close=9.5 ≤ 10
        ]
    )
    readings = [eng.update(b) for b in bars]
    assert readings[1].values["signal"] == 1.0


def test_three_white_soldiers() -> None:
    tws = create("pattern.three_white_soldiers")
    bars = bars_from_ohlcv(
        [
            (10, 12, 10, 11, 100),    # open=10, close=11 (bullish)
            (10.5, 13, 10.5, 12.5, 100),   # open inside prev body (10..11), close=12.5 > 11
            (11.5, 14, 11.5, 13.5, 100),   # open inside prev body (10.5..12.5), close=13.5 > 12.5
        ]
    )
    readings = [tws.update(b) for b in bars]
    assert readings[2].values["signal"] == 1.0


def test_hammer_shape() -> None:
    h = create("pattern.hammer")
    bars = bars_from_ohlcv(
        [
            # body 0.2 (10.0 → 10.2), lower wick = 10.0 - 8.0 = 2.0 = 10x body, upper wick ~0.05
            (10.0, 10.25, 8.0, 10.2, 100),
        ]
    )
    readings = [h.update(b) for b in bars]
    assert readings[0].values["signal"] == 1.0


# ---- Chart patterns -----


def test_double_top_detects_and_confirms() -> None:
    dt = create("pattern.double_top", window=2, tol_pct=0.05)
    # Construct two equal highs with a strict-minimum trough between, then a close
    # below the trough's low. Each peak's high (110) must be the unique max in its
    # surrounding 5-bar window, and the trough's low (100.5) must be the unique min.
    rows = [
        (100, 100.5, 99.5, 100, 100),
        (100, 101, 99.5, 101, 100),
        (101, 110, 101, 109, 100),     # peak 1
        (109, 109.5, 105, 106, 100),
        (106, 106, 100.5, 102, 100),   # trough (strict min L=100.5)
        (102, 107, 102, 106, 100),
        (106, 110, 106, 109, 100),     # peak 2
        (109, 109.5, 105, 106, 100),
        (106, 106, 99, 99.5, 100),     # close below trough — confirms
    ]
    bars = bars_from_ohlcv(rows)
    readings = [dt.update(b) for b in bars]
    assert any(r.values.get("signal", 0.0) == 1.0 for r in readings)


def test_swing_detector_emits_after_lookahead() -> None:
    from features.patterns._swings import SwingDetector

    sw = SwingDetector(window=2)
    # Construct a clear peak in the middle.
    bars = bars_from_ohlcv(
        [
            (10, 11, 9, 10, 1),
            (10, 12, 9, 10, 1),
            (10, 15, 9, 10, 1),  # peak
            (10, 13, 9, 10, 1),
            (10, 12, 9, 10, 1),
        ]
    )
    confirmed: list = []
    for b in bars:
        confirmed.extend(sw.push(b))
    # The peak (bar 2 by index) should have been confirmed once we've seen 2 more bars.
    highs = [s for s in confirmed if s.kind == "high"]
    assert len(highs) == 1
    assert highs[0].price == 15
