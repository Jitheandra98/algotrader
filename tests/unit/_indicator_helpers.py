"""Helpers for indicator tests — build synthetic bars from price lists."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas import AssetClass, Bar, BarTimeframe, Instrument

UTC = timezone.utc


def spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


def make_bar(
    ts: datetime,
    *,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 1000.0,
    instrument: Instrument | None = None,
) -> Bar:
    return Bar(
        instrument=instrument or spy(),
        timeframe=BarTimeframe.M1,
        timestamp=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


def bars_from_closes(closes: list[float], start: datetime | None = None) -> list[Bar]:
    """Build bars from a close-only list. O = prev_C; H and L are widened to enclose
    both open and close so the OHLC ordering validator accepts the bar regardless of
    trend direction."""
    start = start or datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    out: list[Bar] = []
    prev: float | None = None
    for i, c in enumerate(closes):
        o = prev if prev is not None else c
        hi = max(o, c) + 0.1
        lo = min(o, c) - 0.1
        out.append(make_bar(start + timedelta(minutes=i), o=o, h=hi, l=lo, c=c))
        prev = c
    return out


def bars_from_ohlcv(
    rows: list[tuple[float, float, float, float, float]],
    start: datetime | None = None,
) -> list[Bar]:
    start = start or datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    return [
        make_bar(start + timedelta(minutes=i), o=o, h=h, l=l, c=c, v=v)
        for i, (o, h, l, c, v) in enumerate(rows)
    ]
