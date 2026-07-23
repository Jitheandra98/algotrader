"""Pattern detectors — candlestick + multi-bar chart patterns.

Each pattern is registered under the ``pattern.<name>`` namespace so they can be
discovered from configuration files alongside indicators.
"""

from features.patterns import candlestick, chart  # noqa: F401  (side-effect: registers)
