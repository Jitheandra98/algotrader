"""Sector exposure cap.

Caps the sum of position notional in any single sector. The instrument-to-sector
mapping comes from :meth:`RiskContext.sector_of`. Backtest/live engines populate
this from a configurable mapping (yaml or DB) — for tests we wire a small
in-memory mapping.

If sector is unknown (``sector_of`` returns None), the position is bucketed
under ``"UNKNOWN"`` and the limit applies to that bucket too.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from core.schemas import Order, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class SectorExposureLimit(RiskLimit):
    name = "sector_exposure_limit"

    def __init__(
        self,
        sector_pct: dict[str, Decimal | float | str] | None = None,
        *,
        default_pct: Decimal | float | str = "0.30",
    ) -> None:
        self._sector_pct: dict[str, Decimal] = {
            k.upper(): Decimal(str(v)) for k, v in (sector_pct or {}).items()
        }
        self._default = Decimal(str(default_pct))
        if self._default <= 0 or self._default > 1:
            raise ValueError("default_pct must be in (0, 1]")
        for k, v in self._sector_pct.items():
            if v <= 0 or v > 1:
                raise ValueError(f"sector pct for {k} must be in (0, 1]")

    def _cap_for(self, sector: str, equity: Decimal) -> Decimal:
        pct = self._sector_pct.get(sector.upper(), self._default)
        return equity * pct

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        sector = ctx.sector_of(signal.instrument) or "UNKNOWN"
        # Sum notional per sector.
        by_sector: defaultdict[str, Decimal] = defaultdict(Decimal)
        for pos in ctx.positions():
            s = ctx.sector_of(pos.instrument) or "UNKNOWN"
            price = pos.current_price or pos.average_price
            by_sector[s] += abs(pos.quantity) * price
        added = bar.close * proposed.quantity
        new_total = by_sector[sector] + added
        cap = self._cap_for(sector, ctx.equity)
        if new_total > cap:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"sector '{sector}' exposure ${new_total:.2f} > cap ${cap:.2f}"
                ),
                metadata={"sector": sector, "total": str(new_total), "cap": str(cap)},
            )
        return None
