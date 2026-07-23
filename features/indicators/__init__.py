"""Concrete indicators. Importing this module registers every indicator with the
registry — so consumers can ``from features import create; create("rsi", period=14)``.
"""

from features.indicators import (  # noqa: F401  (side-effect: registers)
    bands,
    counting,
    directional,
    moving_averages,
    oscillators,
    regression,
    stops,
    volume,
)
