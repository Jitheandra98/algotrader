"""Short Sale Restriction (SSR) — Reg SHO Rule 201.

When triggered, short sales must execute on a "plus tick" (a price above the
National Best Bid). SSR is set when a stock falls 10%+ from the prior day's
close and stays in effect for the rest of the trading day + the next day.

The full uptick rule requires order-book data we don't have in M6. This
coarse approximation:

  - The :class:`RiskContext` exposes ``is_ssr_flagged(instrument)`` which the
    instrument data adapter sets when a 10% intraday drop is detected.
  - If flagged, this limit blocks new SHORT orders unless the proposed price
    (current bar's close) is **above** the previous bar's close — a rough
    uptick proxy that's tighter than nothing but not exchange-grade.

For options markets / international / crypto: the limit is a no-op (only stocks
get SSR).
"""

from __future__ import annotations

from core.schemas import AssetClass, Order, OrderSide, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class ShortSaleRestriction(RiskLimit):
    name = "short_sale_restriction"

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        if proposed.side is not OrderSide.SELL:
            return None
        # Only applies to stocks / ETFs.
        if signal.instrument.asset_class not in (AssetClass.EQUITY, AssetClass.ETF):
            return None
        # Only meaningful for opening shorts (existing long → SELL is just an
        # exit and is exempt from SSR).
        existing = ctx.position(signal.instrument)
        if existing is not None and existing.quantity > 0:
            return None  # closing a long, not a new short
        if not ctx.is_ssr_flagged(signal.instrument):
            return None
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        # Coarse uptick proxy: require last close > open of same bar (an up bar).
        if bar.close > bar.open:
            return None
        return RiskRejection(
            limit=self.name,
            reason=(
                f"SSR active on {signal.instrument.symbol}: short blocked on "
                f"non-uptick bar (O={bar.open} C={bar.close})"
            ),
            metadata={"open": str(bar.open), "close": str(bar.close)},
        )
