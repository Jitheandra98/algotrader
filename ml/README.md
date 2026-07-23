# `ml/` — ML overlay

Optional signal-quality classifier that gates emitted signals on a model's
`P(win)` prediction. The whole pipeline works without ML active — wire the
:class:`MLSignalFilter` only when you have a trained model.

## Pipeline

```
strategy.on_bar ─► signal ─► [SignalFilter chain] ─► risk ─► broker
                                  │
                                  └── MLSignalFilter (this module)
                                          │
                                          ├── FeatureExtractor: signal → {name: value}
                                          └── SignalQualityModel.predict_proba(X)[:, 1] >= threshold
```

Training is offline (notebooks): journal + feature store → walk-forward CV →
fitted model → save → load at runtime → wire to filter.

## Components

| File              | Purpose |
|-------------------|---------|
| `feature_store.py` | Per-signal feature snapshots; JSONL persistence |
| `models.py`        | `SignalQualityModel` ABC + scikit-learn HistGradientBoosting wrapper |
| `dataset.py`       | Join store + outcomes → `(X, y)` matrices, no-leakage |
| `training.py`      | `WalkForwardTrainer` — per-fold AUC / log-loss / hit-rate |
| `filter.py`        | `MLSignalFilter` — slots into M5 signal-filter chain |

## Quick walk-forward training (notebook usage)

```python
from datetime import datetime, timezone
from decimal import Decimal
from ml import FeatureStore, DatasetBuilder, GBSignalQualityModel, WalkForwardTrainer
from ml.dataset import TradeOutcome
from journal import TradeJournal

# 1. Load persisted feature snapshots + trade journal.
store = FeatureStore.load_jsonl("artifacts/features.jsonl")
journal = TradeJournal.load_jsonl("artifacts/journal.jsonl")

# 2. Build training dataset.
outcomes = [TradeOutcome(signal_id=e.signal_id, pnl=e.pnl, exit_time=e.exit_time)
            for e in journal.all()]
dataset = DatasetBuilder(store=store).build(outcomes)
print(f"dataset: {len(dataset)} samples, {dataset.n_features} features, "
      f"win rate {dataset.y.mean():.2%}")

# 3. Walk-forward CV.
trainer = WalkForwardTrainer(
    model_factory=lambda: GBSignalQualityModel(max_iter=200),
    is_size=500, oos_size=100, anchored=True,
)
result = trainer.train(dataset)
for f in result.folds:
    print(f"fold {f.fold}: AUC={f.auc:.3f} acc={f.accuracy:.3f} base_rate={f.base_rate:.3f}")
print(f"mean AUC: {result.mean_auc:.3f}")

# 4. Save the final model fit on all data.
result.final_model.save("artifacts/model.joblib")
```

## Live filter wiring

```python
from ml import GBSignalQualityModel, MLSignalFilter
from execution import LiveEngine

model = GBSignalQualityModel.load("artifacts/model.joblib")

def extract_features(signal):
    # Pull from the live context's indicator readings.
    rsi = ctx.indicator(f"{signal.instrument.key}:rsi(period=14)")
    atr = ctx.indicator(f"{signal.instrument.key}:atr(period=14)")
    if rsi is None or not rsi.ready or atr is None or not atr.ready:
        return None
    return {"rsi": rsi.values["value"], "atr": atr.values["value"]}

filter_ = MLSignalFilter(model=model, feature_extractor=extract_features, threshold=0.55)
engine = LiveEngine(..., signal_filters=[filter_])
```

## Safety guarantees

The brief mandates **strict walk-forward validation** to guard against curve
fitting. The trainer enforces this by index — each fold's IS window strictly
precedes its OOS window. There is no random k-fold mode, by design.

The :class:`MLSignalFilter` fails **open** (passes signals through) when:
  - no model is loaded
  - the model has no feature names
  - the feature extractor returns ``None`` for a signal
  - prediction itself raises

Failing open avoids the foot-gun where a misconfigured ML model silently
disables every trade. Logs are emitted on every failure mode.

## What's not in M9

- **Online learning / drift detection**: the model is static after `fit()`.
  M10 wires periodic re-training.
- **Feature engineering automation**: feature extraction is caller-supplied.
- **Multi-class outcomes** (win / break-even / loss): binary only.
- **Probability calibration on small datasets**: `CalibratedClassifierCV` needs
  enough OOS data per CV fold. Disable calibration (`calibrate=False`) on small
  training sets.
