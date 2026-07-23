"""Stub RiskContext for testing individual limits without spinning up the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.schemas import AssetClass, Bar, BarTimeframe, Instrument, Position

UTC = timezone.utc


@dataclass
class StubRiskContext:
    """Test double for :class:`risk.base.RiskContext` — set whichever fields the
    limit under test reads."""

    now: datetime = field(default_factory=lambda: datetime(2026, 5, 25, 13, 30, tzinfo=UTC))
    cash: Decimal = Decimal("100000")
    equity: Decimal = Decimal("100000")
    account_value: Decimal = Decimal("100000")
    is_margin_account: bool = True
    _positions: list[Position] = field(default_factory=list)
    _last_bars: dict[str, Bar] = field(default_factory=dict)
    _atrs: dict[str, Decimal] = field(default_factory=dict)
    _trade_pnls: list[Decimal] = field(default_factory=list)
    _equity_snapshots: dict[datetime, Decimal] = field(default_factory=dict)
    _ssr_flags: set[str] = field(default_factory=set)
    _sector_map: dict[str, str] = field(default_factory=dict)
    _correlations: dict[tuple[str, str], float] = field(default_factory=dict)

    def positions(self) -> list[Position]:
        return list(self._positions)

    def position(self, instrument: Instrument) -> Position | None:
        for p in self._positions:
            if p.instrument.key == instrument.key:
                return p
        return None

    def last_bar(self, instrument: Instrument) -> Bar | None:
        return self._last_bars.get(instrument.key)

    def atr(self, instrument: Instrument) -> Decimal | None:
        return self._atrs.get(instrument.key)

    def recent_trade_pnls(self, n: int) -> list[Decimal]:
        return list(self._trade_pnls[-n:])

    def realized_pnl_since(self, since: datetime) -> Decimal:
        return Decimal("0")

    def equity_at(self, ts: datetime) -> Decimal | None:
        # Return the closest at-or-after snapshot.
        for key in sorted(self._equity_snapshots):
            if key >= ts:
                return self._equity_snapshots[key]
        return None

    def is_ssr_flagged(self, instrument: Instrument) -> bool:
        return instrument.key in self._ssr_flags

    def sector_of(self, instrument: Instrument) -> str | None:
        return self._sector_map.get(instrument.symbol) or self._sector_map.get(instrument.key)

    def correlation(self, a: Instrument, b: Instrument) -> float | None:
        return self._correlations.get((a.key, b.key)) or self._correlations.get((b.key, a.key))

    # ---- mutators for tests ----

    def with_bar(self, instrument: Instrument, *, open=100.0, high=102.0, low=98.0, close=101.0, volume=1000.0) -> "StubRiskContext":
        self._last_bars[instrument.key] = Bar(
            instrument=instrument,
            timeframe=BarTimeframe.D1,
            timestamp=self.now,
            open=Decimal(str(open)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal(str(volume)),
        )
        return self

    def with_position(self, pos: Position) -> "StubRiskContext":
        self._positions.append(pos)
        return self

    def with_atr(self, instrument: Instrument, atr: Decimal | float | str) -> "StubRiskContext":
        self._atrs[instrument.key] = Decimal(str(atr))
        return self

    def with_trade_pnls(self, pnls: list[Decimal | float | str]) -> "StubRiskContext":
        self._trade_pnls = [Decimal(str(p)) for p in pnls]
        return self

    def with_equity_snapshot(self, ts: datetime, equity: Decimal | float | str) -> "StubRiskContext":
        self._equity_snapshots[ts] = Decimal(str(equity))
        return self

    def with_ssr_flag(self, instrument: Instrument) -> "StubRiskContext":
        self._ssr_flags.add(instrument.key)
        return self

    def with_sector(self, instrument: Instrument, sector: str) -> "StubRiskContext":
        self._sector_map[instrument.symbol] = sector
        self._sector_map[instrument.key] = sector
        return self

    def with_correlation(self, a: Instrument, b: Instrument, rho: float) -> "StubRiskContext":
        self._correlations[(a.key, b.key)] = rho
        return self


def make_inst(symbol: str = "SPY", asset_class: AssetClass = AssetClass.ETF) -> Instrument:
    return Instrument(symbol=symbol, asset_class=asset_class)


def make_position(
    instrument: Instrument,
    *,
    qty: Decimal | float | str,
    avg_price: Decimal | float | str = "100",
    current_price: Decimal | float | str | None = None,
    strategy_id: str | None = None,
) -> Position:
    return Position(
        instrument=instrument,
        quantity=Decimal(str(qty)),
        average_price=Decimal(str(avg_price)),
        current_price=Decimal(str(current_price)) if current_price is not None else None,
        strategy_id=strategy_id,
    )
