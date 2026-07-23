"""Feature & signal engineering layer.

Stateful indicators that consume :class:`core.schemas.Bar` (or :class:`Tick`) and
produce :class:`IndicatorReading` values. Strategies subscribe to readings via the
bus (in production) or call them in-process (in backtests).

Design notes:
  - Each indicator is single-instrument. Multi-instrument fan-out is the
    caller's responsibility (the strategy engine wires it up in M5).
  - Internal compute uses ``float`` for speed; readings carry floats. Strategies
    convert back to ``Decimal`` only when comparing against actual prices.
  - Every indicator has a ``version`` string so a cache (M9) can invalidate
    correctly when math changes.
  - Public entry points: :class:`Indicator`, :class:`IndicatorReading`,
    :func:`create` (from the registry).
"""

from features.base import Indicator, IndicatorReading
from features.registry import REGISTRY, create, names, register

# Ensure all indicators register themselves when the package is imported.
from features import indicators as _indicators  # noqa: F401
from features import patterns as _patterns  # noqa: F401

from features.relative_strength import RelativeStrength, RSReading

__all__ = [
    "Indicator",
    "IndicatorReading",
    "REGISTRY",
    "RelativeStrength",
    "RSReading",
    "create",
    "names",
    "register",
]
