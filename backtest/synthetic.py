"""Synthetic price-series generators for backtest demos and tests.

Generates OHLCV daily bars via geometric Brownian motion (GBM). Deterministic
given a seed.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from core.schemas import AssetClass, Bar, BarTimeframe, Instrument

UTC = timezone.utc


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def synthetic_daily_bars(
    instrument: Instrument,
    *,
    start: datetime,
    years: float = 5.0,
    initial_price: float = 100.0,
    annual_drift: float = 0.08,
    annual_vol: float = 0.20,
    intraday_range_pct: float = 0.015,
    base_volume: float = 1_000_000.0,
    seed: int = 42,
) -> list[Bar]:
    """Daily OHLCV via GBM. Weekends are skipped. Volume gets a log-normal jitter.

    ``annual_drift`` and ``annual_vol`` are continuously compounded — sensible
    defaults for "large-cap equity-like" series. Set ``annual_drift=0`` for a
    pure noise process.
    """
    rng = random.Random(seed)
    n_calendar_days = int(years * 365.25)
    dt = 1.0 / 252.0
    mu = annual_drift
    sigma = annual_vol

    bars: list[Bar] = []
    price = initial_price
    current = start
    end = start + timedelta(days=n_calendar_days)
    while current < end:
        if not _is_weekday(current.date()):
            current += timedelta(days=1)
            continue
        z = rng.gauss(0.0, 1.0)
        next_price = price * math.exp((mu - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * z)

        # Build OHLC around the day's path. Open ≈ prev close with overnight jitter,
        # close is the GBM step, high/low jitter by intraday_range_pct.
        o = price * (1.0 + rng.gauss(0.0, 0.001))
        c = next_price
        # Range expands when |return| is large.
        rng_pct = max(intraday_range_pct, abs(c / o - 1.0) * 1.5)
        max_oc = max(o, c)
        min_oc = min(o, c)
        h = max_oc * (1.0 + abs(rng.gauss(0.0, rng_pct / 2)))
        l = min_oc * (1.0 - abs(rng.gauss(0.0, rng_pct / 2)))

        # Volume: log-normal around base.
        v = base_volume * math.exp(rng.gauss(0.0, 0.4))

        bars.append(
            Bar(
                instrument=instrument,
                timeframe=BarTimeframe.D1,
                timestamp=datetime.combine(current.date(), datetime.min.time(), tzinfo=UTC),
                open=Decimal(str(round(o, 4))),
                high=Decimal(str(round(h, 4))),
                low=Decimal(str(round(l, 4))),
                close=Decimal(str(round(c, 4))),
                volume=Decimal(str(round(v, 0))),
            )
        )
        price = next_price
        current += timedelta(days=1)

    return bars


def default_spy_instrument() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")
