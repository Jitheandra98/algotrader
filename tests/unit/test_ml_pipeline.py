"""Dataset builder + model + walk-forward trainer integration tests."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import numpy as np
import pytest

from ml import (
    DatasetBuilder,
    FeatureRecord,
    FeatureStore,
    GBSignalQualityModel,
    WalkForwardTrainer,
)
from ml.dataset import TradeOutcome

UTC = timezone.utc


def _build_synthetic_store(n: int = 200, seed: int = 0) -> tuple[FeatureStore, list[TradeOutcome]]:
    """Construct a synthetic dataset where feature ``rsi`` is correlated with
    win probability — the model should learn it."""
    rng = np.random.default_rng(seed)
    store = FeatureStore()
    outcomes: list[TradeOutcome] = []
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        sig_id = uuid4()
        # RSI uniformly in [10, 90]. Lower RSI → higher win probability.
        rsi = float(rng.uniform(10, 90))
        atr = float(rng.uniform(0.5, 3.0))
        store.add(
            FeatureRecord(
                signal_id=sig_id,
                strategy_id="rsi_mr",
                instrument_key="SPY|ETF",
                timestamp=base + timedelta(minutes=i),
                direction="LONG",
                features={"rsi": rsi, "atr": atr},
            )
        )
        # P(win) = 0.92 at RSI=10, 0.08 at RSI=90 — stronger signal so the
        # model learns the relationship reliably with N=400.
        p_win = 0.92 - (rsi - 10) / 80 * 0.84
        won = rng.random() < p_win
        outcomes.append(
            TradeOutcome(
                signal_id=str(sig_id),
                pnl=Decimal("10") if won else Decimal("-10"),
                exit_time=base + timedelta(minutes=i + 60),
            )
        )
    return store, outcomes


def test_dataset_builds_X_y_aligned() -> None:
    store, outcomes = _build_synthetic_store(50, seed=1)
    ds = DatasetBuilder(store=store).build(outcomes)
    assert len(ds) == 50
    assert ds.n_features == 2
    assert set(ds.feature_names) == {"rsi", "atr"}
    assert ds.X.shape == (50, 2)
    assert ds.y.shape == (50,)


def test_dataset_drops_records_with_no_outcome() -> None:
    store, outcomes = _build_synthetic_store(20, seed=2)
    # Add an outcome whose signal_id doesn't exist in the store.
    outcomes.append(TradeOutcome(signal_id=str(uuid4()), pnl=Decimal("5"), exit_time=datetime(2026, 6, 1, tzinfo=UTC)))
    ds = DatasetBuilder(store=store).build(outcomes)
    assert len(ds) == 20  # phantom outcome dropped


def test_dataset_drops_leaky_records() -> None:
    """Feature timestamp must be < trade exit_time."""
    store = FeatureStore()
    sig = uuid4()
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    store.add(FeatureRecord(
        signal_id=sig, strategy_id="s", instrument_key="SPY|ETF",
        timestamp=ts, direction="LONG", features={"rsi": 25.0},
    ))
    # Exit time BEFORE feature timestamp — should be filtered.
    outcomes = [TradeOutcome(signal_id=str(sig), pnl=Decimal("1"), exit_time=ts - timedelta(minutes=1))]
    ds = DatasetBuilder(store=store).build(outcomes)
    assert len(ds) == 0


def test_model_predicts_signal_from_correlated_feature() -> None:
    store, outcomes = _build_synthetic_store(600, seed=3)
    ds = DatasetBuilder(store=store).build(outcomes)
    model = GBSignalQualityModel(max_iter=200, max_depth=4, min_samples_leaf=10, calibrate=False, early_stopping=False)
    model.fit(ds.X, ds.y, feature_names=ds.feature_names)
    # Build the test row by feature name — DatasetBuilder sorts columns
    # alphabetically (atr before rsi), so we must respect that order.
    rsi_col = ds.feature_names.index("rsi")
    atr_col = ds.feature_names.index("atr")
    low = np.zeros(2)
    low[rsi_col] = 15.0
    low[atr_col] = 1.0
    high = np.zeros(2)
    high[rsi_col] = 85.0
    high[atr_col] = 1.0
    proba = model.predict_proba(np.array([low, high]))
    p_low = proba[0, 1]
    p_high = proba[1, 1]
    assert p_low > p_high, f"expected low RSI to predict higher P(win): {p_low} vs {p_high}"


def test_walk_forward_trainer_runs_multiple_folds() -> None:
    store, outcomes = _build_synthetic_store(300, seed=4)
    ds = DatasetBuilder(store=store).build(outcomes)
    trainer = WalkForwardTrainer(
        model_factory=lambda: GBSignalQualityModel(max_iter=50, calibrate=False),
        is_size=100,
        oos_size=50,
    )
    result = trainer.train(ds)
    assert len(result.folds) >= 3
    # On a correlated synthetic dataset, AUC should beat random (0.5).
    assert result.mean_auc is not None
    assert result.mean_auc > 0.55
    assert result.final_model is not None


def test_walk_forward_handles_small_dataset() -> None:
    store, outcomes = _build_synthetic_store(20, seed=5)
    ds = DatasetBuilder(store=store).build(outcomes)
    trainer = WalkForwardTrainer(
        model_factory=lambda: GBSignalQualityModel(max_iter=20),
        is_size=100, oos_size=50,
    )
    result = trainer.train(ds)
    # Too small → no folds returned.
    assert result.folds == []
    assert result.final_model is None


def test_model_save_load_round_trip(tmp_path) -> None:
    store, outcomes = _build_synthetic_store(100, seed=6)
    ds = DatasetBuilder(store=store).build(outcomes)
    model = GBSignalQualityModel(max_iter=50, calibrate=False)
    model.fit(ds.X, ds.y, feature_names=ds.feature_names)
    path = tmp_path / "model.joblib"
    model.save(path)
    loaded = GBSignalQualityModel.load(path)
    assert loaded.feature_names == ds.feature_names
    p1 = model.predict_proba(ds.X[:5])
    p2 = loaded.predict_proba(ds.X[:5])
    np.testing.assert_array_almost_equal(p1, p2)
