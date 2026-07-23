"""MLSignalFilter — block signals whose ML-predicted P(win) is below threshold.

Slots into the M5 signal-filter chain. Safe behaviors:

  - **No model loaded** → pass everything through unchanged (so a misconfigured
    deployment doesn't silently disable signals).
  - **Feature extractor returns None** → pass through (with a warn-level log).
  - **Predicted P(win) < threshold** → drop the signal.

The feature extractor is a caller-supplied callable that turns a :class:`Signal`
into the feature vector matching the model's training schema. We pass it in
because the filter doesn't have access to indicator readings on its own — the
caller wires it up to the live context.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Callable

import numpy as np

from core.logging import get_logger
from core.schemas import Direction, Signal
from ml.models import SignalQualityModel
from strategies.ensemble import SignalFilter

log = get_logger(__name__)


FeatureExtractor = Callable[[Signal], dict[str, float] | None]


class MLSignalFilter(SignalFilter):
    """Gate non-HOLD signals on the model's P(win) prediction."""

    def __init__(
        self,
        *,
        model: SignalQualityModel | None,
        feature_extractor: FeatureExtractor,
        threshold: float = 0.55,
        ignore_directions: tuple[Direction, ...] = (Direction.EXIT, Direction.HOLD),
    ) -> None:
        if not 0 < threshold < 1:
            raise ValueError("threshold must be in (0, 1)")
        self._model = model
        self._extract = feature_extractor
        self._threshold = threshold
        self._ignore = set(ignore_directions)

    def filter(self, signals: Sequence[Signal], *, now: datetime) -> list[Signal]:  # noqa: ARG002
        if self._model is None:
            return list(signals)  # no-op when no model loaded
        feature_names = self._model.feature_names
        if not feature_names:
            return list(signals)

        out: list[Signal] = []
        for s in signals:
            if s.direction in self._ignore:
                out.append(s)
                continue
            features = self._extract(s)
            if features is None:
                log.warning("ml_filter_no_features", signal_id=str(s.id))
                out.append(s)  # fail-open
                continue
            row = np.array([[float(features.get(n, 0.0)) for n in feature_names]], dtype=np.float64)
            try:
                p_win = float(self._model.predict_proba(row)[0, 1])
            except Exception:
                log.exception("ml_filter_predict_failed", signal_id=str(s.id))
                out.append(s)
                continue
            if p_win >= self._threshold:
                out.append(
                    s.model_copy(update={"metadata": {**s.metadata, "ml_p_win": p_win}})
                )
            else:
                log.info(
                    "ml_filter_dropped",
                    signal_id=str(s.id),
                    p_win=p_win,
                    threshold=self._threshold,
                )
        return out
