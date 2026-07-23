# Research notebooks

Jupyter notebooks for exploratory work. **Not** part of the lint/test gate —
these are scratchpads, not production code. Notebooks that prove out something
useful should graduate to `research/helpers.py` (small utilities) or into the
proper module hierarchy (`features`, `strategies`, etc.).

## Setup

```bash
poetry install --with dev
poetry run jupyter lab
```

## Suggested notebook flow

1. **01-data-exploration**: load a synthetic fixture (or DB-backed bars), poke at
   distributions of returns, volatility, gap behavior. The `research.helpers.load_synthetic_spy()`
   helper is the fast path.

2. **02-strategy-debugging**: instantiate one of the M5 strategies, step through
   bars, inspect the indicator readings the strategy actually saw. Useful when
   "the strategy should have fired but didn't."

   ```python
   from research.helpers import load_synthetic_spy, run_backtest_quick
   _, bars = load_synthetic_spy()
   result = run_backtest_quick(
       strategy_name="rsi_mean_reversion",
       parameters={"rsi_period": 14, "oversold": 30, "overbought": 70, "trend_period": 50, "atr_period": 14},
       bars=bars,
   )
   print(result.kpis)
   ```

3. **03-walk-forward-cv**: train an `MLSignalQualityModel` on a feature
   store + journal, run walk-forward CV, inspect per-fold AUC/log-loss.

   ```python
   from ml import FeatureStore, DatasetBuilder, GBSignalQualityModel, WalkForwardTrainer
   from ml.dataset import TradeOutcome

   store = FeatureStore.load_jsonl("artifacts/features.jsonl")
   journal = TradeJournal.load_jsonl("artifacts/journal.jsonl")
   outcomes = [TradeOutcome(signal_id=e.signal_id, pnl=e.pnl, exit_time=e.exit_time) for e in journal.all()]
   dataset = DatasetBuilder(store=store).build(outcomes)

   trainer = WalkForwardTrainer(
       model_factory=lambda: GBSignalQualityModel(max_iter=200),
       is_size=200, oos_size=50, anchored=True,
   )
   result = trainer.train(dataset)
   for f in result.folds:
       print(f)
   print("mean AUC:", result.mean_auc)
   ```

4. **04-feature-importance**: once a model is trained, plot feature importance
   (HistGradientBoosting exposes `feature_importances_` indirectly via
   `inspection.permutation_importance`).

## Convention

Notebooks should be runnable top-to-bottom with no manual cell ordering.
If a notebook needs heavy setup (DB connection, large file load), put the
setup in `research/helpers.py` so the notebook stays readable.
