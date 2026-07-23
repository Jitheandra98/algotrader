"""ML overlay — signal-quality classifier, feature store, walk-forward training.

The ML layer is **optional**. The full strategy/risk/execution pipeline works
without any ML component active. When wired, the :class:`MLSignalFilter` sits
in the signal-filter chain (M5) and gates signals on the classifier's
``P(win)`` prediction.

Public surface:
  - :class:`FeatureStore`        — write/query feature vectors per signal.
  - :class:`SignalQualityModel`  — model ABC.
  - :class:`GBSignalQualityModel` — scikit-learn HistGradientBoostingClassifier.
  - :class:`Dataset` + :class:`DatasetBuilder` — (X, y) construction from
    store + trade outcomes.
  - :class:`WalkForwardTrainer`  — strict windowed CV.
  - :class:`MLSignalFilter`      — :class:`strategies.SignalFilter` impl.
"""

from ml.dataset import Dataset, DatasetBuilder
from ml.feature_store import FeatureRecord, FeatureStore
from ml.filter import MLSignalFilter
from ml.models import GBSignalQualityModel, SignalQualityModel
from ml.training import FoldMetrics, WalkForwardTrainer

__all__ = [
    "Dataset",
    "DatasetBuilder",
    "FeatureRecord",
    "FeatureStore",
    "FoldMetrics",
    "GBSignalQualityModel",
    "MLSignalFilter",
    "SignalQualityModel",
    "WalkForwardTrainer",
]
