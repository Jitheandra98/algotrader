"""MLSignalFilter behavior tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest

from core.schemas import AssetClass, Direction, Instrument, Signal
from ml import MLSignalFilter, SignalQualityModel

UTC = timezone.utc


class _StubModel(SignalQualityModel):
    def __init__(self, p_win_for_rsi: dict[float, float], feature_names=("rsi", "atr")) -> None:
        self._map = p_win_for_rsi
        self._features = list(feature_names)

    def fit(self, X, y, feature_names=None) -> None:
        if feature_names:
            self._features = list(feature_names)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros((len(X), 2))
        for i, row in enumerate(X):
            rsi = float(row[0])
            p = self._map.get(rsi, 0.5)
            out[i] = [1 - p, p]
        return out

    @property
    def feature_names(self) -> list[str]:
        return self._features

    def save(self, path):
        raise NotImplementedError

    @classmethod
    def load(cls, path):
        raise NotImplementedError


def _signal(direction=Direction.LONG, conviction=0.8) -> Signal:
    return Signal(
        strategy_id="t",
        instrument=Instrument(symbol="SPY", asset_class=AssetClass.ETF),
        direction=direction,
        conviction=conviction,
        rationale="t",
    )


def test_filter_drops_below_threshold() -> None:
    model = _StubModel({25.0: 0.3, 75.0: 0.8})
    f = MLSignalFilter(
        model=model,
        feature_extractor=lambda sig: {"rsi": float(sig.metadata.get("rsi", 50)), "atr": 1.0},
        threshold=0.6,
    )
    s_bad = _signal().model_copy(update={"metadata": {"rsi": 25.0}})
    s_good = _signal().model_copy(update={"metadata": {"rsi": 75.0}})
    out = f.filter([s_bad, s_good], now=datetime.now(tz=UTC))
    assert len(out) == 1
    assert out[0].metadata["ml_p_win"] == 0.8


def test_filter_passes_through_when_no_model() -> None:
    f = MLSignalFilter(model=None, feature_extractor=lambda s: {}, threshold=0.5)
    sigs = [_signal()]
    assert f.filter(sigs, now=datetime.now(tz=UTC)) == sigs


def test_filter_passes_exit_and_hold_unchanged() -> None:
    model = _StubModel({})
    f = MLSignalFilter(
        model=model,
        feature_extractor=lambda s: {"rsi": 50.0, "atr": 1.0},
        threshold=0.5,
    )
    exits = [_signal(direction=Direction.EXIT), _signal(direction=Direction.HOLD)]
    assert f.filter(exits, now=datetime.now(tz=UTC)) == exits


def test_filter_fail_open_when_extractor_returns_none() -> None:
    model = _StubModel({})
    f = MLSignalFilter(
        model=model,
        feature_extractor=lambda s: None,  # extractor can't compute features
        threshold=0.99,  # would normally drop everything
    )
    sigs = [_signal()]
    # Fail-open: signal passes through with a warning logged.
    assert f.filter(sigs, now=datetime.now(tz=UTC)) == sigs


def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="threshold"):
        MLSignalFilter(model=None, feature_extractor=lambda s: {}, threshold=0.0)
    with pytest.raises(ValueError, match="threshold"):
        MLSignalFilter(model=None, feature_extractor=lambda s: {}, threshold=1.0)
