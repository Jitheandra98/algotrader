"""Walk-forward training with per-fold metrics.

Uses :class:`backtest.WalkForwardRunner` for the index slicing — same logic as
the strategy backtest splits — to guarantee training never sees future data.

Each fold trains on the IS window, predicts on the OOS window, records:
  - AUC (ROC area under curve)
  - log-loss
  - accuracy
  - hit rate (predicted-win precision when threshold = 0.5)

The model is *re-fit* per fold to avoid leakage. The final returned model is
fit on the **last** (in-sample, out-of-sample) merged window — that's the model
most representative of "all data through now."
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from backtest.walk_forward import WalkForwardRunner
from core.logging import get_logger
from ml.dataset import Dataset
from ml.models import SignalQualityModel

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    fold: int
    n_train: int
    n_test: int
    auc: float | None
    log_loss: float | None
    accuracy: float
    hit_rate: float        # precision at threshold 0.5 (or NaN if no positives predicted)
    base_rate: float       # OOS y mean


@dataclass(frozen=True, slots=True)
class TrainingResult:
    folds: list[FoldMetrics]
    mean_auc: float | None
    mean_log_loss: float | None
    mean_accuracy: float
    final_model: SignalQualityModel | None
    feature_names: list[str] = field(default_factory=list)


class WalkForwardTrainer:
    """Drives walk-forward CV. The trainer is model-agnostic — pass any
    :class:`SignalQualityModel` factory."""

    def __init__(
        self,
        *,
        model_factory,                   # () -> SignalQualityModel
        is_size: int,
        oos_size: int,
        step: int | None = None,
        anchored: bool = False,
    ) -> None:
        if is_size < 10:
            raise ValueError("is_size too small for meaningful training (min 10)")
        if oos_size < 1:
            raise ValueError("oos_size must be >= 1")
        self._factory = model_factory
        self._runner: WalkForwardRunner[int] = WalkForwardRunner(
            is_size=is_size, oos_size=oos_size, step=step, anchored=anchored
        )

    def train(self, dataset: Dataset, *, fit_final_on_all: bool = True) -> TrainingResult:
        n = len(dataset)
        if n < (self._runner._is + self._runner._oos):  # noqa: SLF001
            log.warning("walk_forward_dataset_too_small", n=n)
            return TrainingResult([], None, None, 0.0, None, list(dataset.feature_names))

        indices = list(range(n))
        folds: list[FoldMetrics] = []
        for window in self._runner.windows(indices):
            X_train = dataset.X[window.in_sample]
            y_train = dataset.y[window.in_sample]
            X_test = dataset.X[window.out_of_sample]
            y_test = dataset.y[window.out_of_sample]

            # Skip folds with single-class training data (model can't learn).
            if len(set(y_train.tolist())) < 2:
                continue

            model = self._factory()
            model.fit(X_train, y_train, feature_names=dataset.feature_names)
            proba = model.predict_proba(X_test)[:, 1]
            metrics = _fold_metrics(window.index, y_train, y_test, proba)
            folds.append(metrics)

        if not folds:
            return TrainingResult([], None, None, 0.0, None, list(dataset.feature_names))

        aucs = [f.auc for f in folds if f.auc is not None]
        logls = [f.log_loss for f in folds if f.log_loss is not None]
        accs = [f.accuracy for f in folds]
        mean_auc = float(np.mean(aucs)) if aucs else None
        mean_log_loss = float(np.mean(logls)) if logls else None
        mean_acc = float(np.mean(accs))

        final_model = None
        if fit_final_on_all and len(set(dataset.y.tolist())) >= 2:
            final_model = self._factory()
            final_model.fit(dataset.X, dataset.y, feature_names=dataset.feature_names)

        return TrainingResult(
            folds=folds,
            mean_auc=mean_auc,
            mean_log_loss=mean_log_loss,
            mean_accuracy=mean_acc,
            final_model=final_model,
            feature_names=list(dataset.feature_names),
        )


def _fold_metrics(idx: int, y_train: np.ndarray, y_test: np.ndarray, proba: np.ndarray) -> FoldMetrics:
    from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

    n_train = len(y_train)
    n_test = len(y_test)

    pred = (proba >= 0.5).astype(int)
    acc = float(accuracy_score(y_test, pred))

    # AUC and log-loss require both classes present in y_test.
    try:
        auc = float(roc_auc_score(y_test, proba))
    except ValueError:
        auc = None
    try:
        ll = float(log_loss(y_test, np.clip(proba, 1e-7, 1 - 1e-7)))
    except ValueError:
        ll = None

    if pred.sum() == 0:
        hit_rate = float("nan")
    else:
        hit_rate = float((pred & y_test).sum() / pred.sum())

    base_rate = float(np.mean(y_test)) if len(y_test) else 0.0
    return FoldMetrics(
        fold=idx,
        n_train=n_train,
        n_test=n_test,
        auc=auc,
        log_loss=ll,
        accuracy=acc,
        hit_rate=hit_rate,
        base_rate=base_rate,
    )
