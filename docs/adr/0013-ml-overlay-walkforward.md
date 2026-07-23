# ADR-0013 — ML overlay uses strict walk-forward CV and fails open

**Status:** Accepted (M9)
**Date:** 2026-05-25

## Context

The brief specifies:

  > "an optional ML signal-quality filter using scikit-learn (gradient boosting
  >  baseline) with strict walk-forward validation to guard against curve-fitting."

Two non-trivial design questions:

  1. **How do we enforce "no look-ahead"?** Random k-fold CV is the default in
     scikit-learn but for time-series data it leaks the future into the past.
  2. **What happens at runtime when the model is broken / missing / mis-aligned
     with the live features?**

## Decision

- **Training uses ``WalkForwardTrainer``** which reuses M4's
  ``WalkForwardRunner`` for the index splits. Each fold's training indices are
  strictly less than its test indices. There is no random-shuffle option.
- **The classifier ships with calibration on by default** —
  ``CalibratedClassifierCV(method="isotonic", cv=3)``. Raw GBM scores are not
  probabilities; without calibration the threshold knob doesn't map to "win
  rate ≥ X." Calibration costs ~3× training time and adds dataset-size
  requirements (≥ ~100 samples per CV fold) — disable for very small datasets.
- **The runtime filter fails open**, not closed. If the model is missing, the
  feature extractor returns None, or prediction raises, the signal passes
  through. Every failure mode emits a structured log. The reasoning: an ML
  outage should degrade us to "trade with original signal" not "stop trading."
  An operator can hard-disable signals via the kill-switch (M6) instead.
- **Feature extraction is caller-supplied**. The filter doesn't know how to
  compute RSI on its own; the caller wires it up to the live indicator
  registry. This keeps the ML module pure-data — easier to test in isolation
  and easier to swap in a different model type later.

## Consequences

- Walk-forward CV gives realistic AUC numbers but smaller training datasets
  per fold than k-fold would. That's the right tradeoff for trading.
- The fail-open behavior is a deliberate departure from the "no bypass for
  risk" rule (ADR-0010). ML is *advisory* — the risk engine is *mandatory*.
  These are different layers with different failure semantics.
- The feature-name ordering is baked into the trained model (`feature_names`
  attribute) and the filter respects it. Renaming or reordering features in
  config is a model-incompatible change — re-train.

## Out of scope

- **Online learning** (continual updates from new fills). M10 will add a
  periodic re-training job; the model itself stays static between updates.
- **Drift detection** (the live feature distribution differs from training).
  We log raw predictions; M10 wires drift metrics.
- **Multi-class outcomes** (win / break-even / loss). Binary only in M9.
- **XGBoost / LightGBM / neural nets**. Subclass `SignalQualityModel` to
  add — the trainer and filter are model-agnostic.
