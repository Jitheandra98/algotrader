"""Indicator base — uniform interface for everything in :mod:`features`.

Every indicator implements::

    indicator.update(bar) -> IndicatorReading

The reading is always returned, even before warmup completes; ``ready=False`` until
the indicator has enough history. This lets callers attach without caring about
warmup semantics — they just check ``ready`` before using ``values``.

Sub-indicators expose named output fields. Examples:
  - SMA → ``{"value": float}``
  - MACD → ``{"macd": float, "signal": float, "hist": float}``
  - Bollinger → ``{"middle": float, "upper": float, "lower": float, "width": float}``

This sidesteps the awkwardness of multi-output indicators returning tuples.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from core.schemas import Bar, Instrument


@dataclass(frozen=True, slots=True)
class IndicatorReading:
    """Snapshot output of one ``update(bar)`` call.

    Carries the instrument and timestamp for routing/persistence, the named output
    fields, and a ``ready`` flag that downstream code should check.
    """

    indicator: str            # registry name, e.g. "rsi"
    instrument: Instrument
    timestamp: datetime
    values: dict[str, float] = field(default_factory=dict)
    ready: bool = False

    def value(self, key: str = "value") -> float | None:
        """Convenience accessor returning ``None`` when not ready or key missing."""
        if not self.ready:
            return None
        return self.values.get(key)


class Indicator(ABC):
    """Stateful single-instrument indicator.

    Subclasses must:
      - set :attr:`name` (the registry key)
      - set :attr:`version` (bump on math change so caches invalidate)
      - declare :attr:`output_keys` for documentation / introspection
      - implement :meth:`_update` which returns the values dict (or None if drop)
      - set :attr:`warmup` (number of bars needed before ``ready=True``)
    """

    name: ClassVar[str] = "indicator"
    version: ClassVar[str] = "1"
    output_keys: ClassVar[tuple[str, ...]] = ("value",)

    def __init__(self) -> None:
        self._count: int = 0
        self.warmup: int = 0  # subclasses override at __init__-time once params bound

    @abstractmethod
    def _update(self, bar: Bar) -> dict[str, float] | None:
        """Process a bar, mutate internal state, return the new output values.

        Return ``None`` only when no reading should be emitted at all (rare —
        typically you return values with ``ready=False`` until warmup).
        """

    def update(self, bar: Bar) -> IndicatorReading:
        values = self._update(bar)
        self._count += 1
        ready = self._count >= self.warmup and values is not None
        return IndicatorReading(
            indicator=self.name,
            instrument=bar.instrument,
            timestamp=bar.timestamp,
            values=values or {},
            ready=ready,
        )

    def reset(self) -> None:
        """Clear all state. Subclasses should override to clear their own buffers
        and call ``super().reset()`` to reset the bar counter."""
        self._count = 0

    @property
    def params(self) -> dict[str, Any]:
        """Override to expose parameters for serialization / cache keys."""
        return {}

    def cache_key(self, instrument: Instrument) -> str:
        """Deterministic cache key for (indicator, params, instrument)."""
        param_str = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}@{self.version}({param_str})/{instrument.key}"
