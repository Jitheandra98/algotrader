"""Signal-quality model — ABC + scikit-learn HistGradientBoostingClassifier wrapper.

Models predict ``P(win | features)``. The :class:`MLSignalFilter` uses this to
gate signals. The interface is narrow:

  - :meth:`fit(X, y)`           — train on labeled examples.
  - :meth:`predict_proba(X)`    — column ``[:, 1]`` is P(win).
  - :meth:`save` / :meth:`load` — pickle round-trip for production loading.

Calibration is opt-in via :class:`sklearn.calibration.CalibratedClassifierCV`,
because raw GB scores are not probabilities — they're decision-function values
that monotonically increase with class confidence but aren't well-calibrated
without isotonic / sigmoid post-processing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from core.logging import get_logger

log = get_logger(__name__)


class SignalQualityModel(ABC):
    """Predicts win probability for a signal."""

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None) -> None: ...

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Returns shape ``(n, 2)`` — column 1 is P(win)."""

    def predict_win(self, X: np.ndarray) -> np.ndarray:
        proba = self.predict_proba(X)
        return proba[:, 1] if proba.ndim == 2 and proba.shape[1] >= 2 else proba

    @property
    @abstractmethod
    def feature_names(self) -> list[str]: ...

    @abstractmethod
    def save(self, path: Path | str) -> None: ...

    @classmethod
    @abstractmethod
    def load(cls, path: Path | str) -> "SignalQualityModel": ...


@dataclass
class GBSignalQualityModel(SignalQualityModel):
    """scikit-learn HistGradientBoostingClassifier wrapper.

    Defaults are sensible for small-to-medium training sets (≤ 100k samples).
    For very large datasets, increase ``max_iter`` and consider XGBoost / LightGBM
    via a new subclass.
    """

    learning_rate: float = 0.05
    max_iter: int = 300
    max_depth: int | None = 6
    min_samples_leaf: int = 20
    l2_regularization: float = 1.0
    early_stopping: bool | str = "auto"
    random_state: int = 42
    calibrate: bool = True   # wrap in CalibratedClassifierCV for valid probabilities

    _model: Any = field(default=None, init=False, repr=False)
    _feature_names: list[str] = field(default_factory=list, init=False, repr=False)

    def _build(self):
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.ensemble import HistGradientBoostingClassifier

        base = HistGradientBoostingClassifier(
            learning_rate=self.learning_rate,
            max_iter=self.max_iter,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            l2_regularization=self.l2_regularization,
            early_stopping=self.early_stopping,
            random_state=self.random_state,
        )
        if self.calibrate:
            return CalibratedClassifierCV(base, method="isotonic", cv=3)
        return base

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None) -> None:
        if X.size == 0:
            raise ValueError("cannot fit on empty X")
        if len(X) != len(y):
            raise ValueError(f"X and y length mismatch: {len(X)} vs {len(y)}")
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        self._model = self._build()
        self._model.fit(X, y)
        log.info("ml_model_trained", n_samples=len(X), n_features=X.shape[1])

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("model not fitted yet — call fit() first")
        return self._model.predict_proba(X)

    @property
    def feature_names(self) -> list[str]:
        return list(self._feature_names)

    def save(self, path: Path | str) -> None:
        if self._model is None:
            raise RuntimeError("nothing to save — model not fitted")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self._model,
                "feature_names": self._feature_names,
                "params": {
                    "learning_rate": self.learning_rate,
                    "max_iter": self.max_iter,
                    "max_depth": self.max_depth,
                    "min_samples_leaf": self.min_samples_leaf,
                    "l2_regularization": self.l2_regularization,
                    "calibrate": self.calibrate,
                },
            },
            p,
        )
        log.info("ml_model_saved", path=str(p))

    @classmethod
    def load(cls, path: Path | str) -> "GBSignalQualityModel":
        data = joblib.load(Path(path))
        params = data.get("params", {})
        inst = cls(**{k: v for k, v in params.items() if k in {
            "learning_rate", "max_iter", "max_depth", "min_samples_leaf",
            "l2_regularization", "calibrate",
        }})
        inst._model = data["model"]
        inst._feature_names = list(data.get("feature_names", []))
        return inst
