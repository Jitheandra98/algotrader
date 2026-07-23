"""Alpaca data adapter — paper / live data for stocks and crypto.

Endpoints (as of 2026):
  Historical bars (stocks):  GET https://data.alpaca.markets/v2/stocks/bars
  Historical bars (crypto):  GET https://data.alpaca.markets/v1beta3/crypto/us/bars
  WebSocket (stocks IEX):    wss://stream.data.alpaca.markets/v2/iex
  WebSocket (stocks SIP):    wss://stream.data.alpaca.markets/v2/sip   (paid)
  WebSocket (crypto):        wss://stream.data.alpaca.markets/v1beta3/crypto/us

Auth:
  - REST: ``APCA-API-KEY-ID`` and ``APCA-API-SECRET-KEY`` headers.
  - WS:   First post-connect message: {"action":"auth","key":"...","secret":"..."}.

The adapter is broker-agnostic from the caller's perspective: it yields ``Tick`` and
``Bar`` from :mod:`core.schemas`. Stocks and crypto share the same code path; the
:class:`AssetClass` on the instrument routes to the right endpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import httpx
import orjson
import websockets
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.logging import get_logger
from core.schemas import AssetClass, Bar, BarTimeframe, Instrument, Tick
from core.secrets import EnvSecretProvider, SecretProvider
from data.base import DataAdapter, HistoricalBarRequest

log = get_logger(__name__)


_TF_TO_ALPACA: dict[BarTimeframe, str] = {
    BarTimeframe.M1: "1Min",
    BarTimeframe.M5: "5Min",
    BarTimeframe.M15: "15Min",
    BarTimeframe.M30: "30Min",
    BarTimeframe.H1: "1Hour",
    BarTimeframe.H4: "4Hour",
    BarTimeframe.D1: "1Day",
    BarTimeframe.W1: "1Week",
}


class AlpacaAdapter(DataAdapter):
    """Alpaca stocks + crypto data adapter."""

    name = "alpaca"

    def __init__(
        self,
        *,
        secret_provider: SecretProvider | None = None,
        rest_base: str = "https://data.alpaca.markets",
        ws_stocks_url: str = "wss://stream.data.alpaca.markets/v2/iex",
        ws_crypto_url: str = "wss://stream.data.alpaca.markets/v1beta3/crypto/us",
        request_timeout_s: float = 30.0,
    ) -> None:
        self._sp: SecretProvider = secret_provider or EnvSecretProvider()
        self._rest_base = rest_base.rstrip("/")
        self._ws_stocks_url = ws_stocks_url
        self._ws_crypto_url = ws_crypto_url
        self._timeout = request_timeout_s
        self._http: httpx.AsyncClient | None = None

    # ---- lifecycle ----

    async def connect(self) -> None:
        if self._http is not None:
            return
        api_key = self._sp.get("alpaca_api_key")
        api_secret = self._sp.get("alpaca_api_secret")
        self._http = httpx.AsyncClient(
            base_url=self._rest_base,
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
                "Accept": "application/json",
            },
            timeout=self._timeout,
        )
        log.info("alpaca_connected", rest_base=self._rest_base)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            log.info("alpaca_closed")

    def _require_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("AlpacaAdapter not connected — call connect() first")
        return self._http

    # ---- historical ----

    async def historical_bars(self, request: HistoricalBarRequest) -> AsyncIterator[Bar]:
        client = self._require_http()
        if request.timeframe not in _TF_TO_ALPACA:
            raise ValueError(f"alpaca: unsupported timeframe {request.timeframe}")

        # Group by asset class — stocks and crypto are different endpoints.
        stock_syms = [i for i in request.instruments if i.asset_class in {AssetClass.EQUITY, AssetClass.ETF}]
        crypto_syms = [i for i in request.instruments if i.asset_class is AssetClass.CRYPTO]

        if stock_syms:
            async for bar in self._historical_paged(
                client,
                path="/v2/stocks/bars",
                instruments=stock_syms,
                request=request,
            ):
                yield bar
        if crypto_syms:
            async for bar in self._historical_paged(
                client,
                path="/v1beta3/crypto/us/bars",
                instruments=crypto_syms,
                request=request,
            ):
                yield bar

    async def _historical_paged(
        self,
        client: httpx.AsyncClient,
        *,
        path: str,
        instruments: Sequence[Instrument],
        request: HistoricalBarRequest,
    ) -> AsyncIterator[Bar]:
        symbols = ",".join(i.symbol for i in instruments)
        sym_to_inst = {i.symbol: i for i in instruments}
        params: dict[str, Any] = {
            "symbols": symbols,
            "timeframe": _TF_TO_ALPACA[request.timeframe],
            "start": request.start.isoformat().replace("+00:00", "Z"),
            "end": request.end.isoformat().replace("+00:00", "Z"),
            "limit": min(request.limit or 10_000, 10_000),
            "feed": "iex",
        }
        if instruments and instruments[0].asset_class is AssetClass.CRYPTO:
            params.pop("feed", None)

        next_token: str | None = None
        while True:
            if next_token:
                params["page_token"] = next_token
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=1, min=1, max=30),
                retry=retry_if_exception_type((httpx.HTTPError,)),
                reraise=True,
            ):
                with attempt:
                    resp = await client.get(path, params=params)
                    if resp.status_code == 429:
                        # Treat as retryable. Tenacity will back off.
                        raise httpx.HTTPStatusError(
                            "rate limited", request=resp.request, response=resp
                        )
                    resp.raise_for_status()
            data = orjson.loads(resp.content)
            bars_by_symbol: dict[str, list[dict[str, Any]]] = data.get("bars", {}) or {}
            for sym, recs in bars_by_symbol.items():
                inst = sym_to_inst.get(sym)
                if inst is None:
                    continue
                for r in recs:
                    yield _bar_from_alpaca(inst, request.timeframe, r)
            next_token = data.get("next_page_token")
            if not next_token:
                return

    # ---- streaming ----

    async def stream_ticks(self, instruments: Sequence[Instrument]) -> AsyncIterator[Tick]:
        # Split by asset class, multiplex.
        stock_syms = [i for i in instruments if i.asset_class in {AssetClass.EQUITY, AssetClass.ETF}]
        crypto_syms = [i for i in instruments if i.asset_class is AssetClass.CRYPTO]

        queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=10_000)
        tasks: list[asyncio.Task[None]] = []
        if stock_syms:
            tasks.append(asyncio.create_task(self._ws_loop(self._ws_stocks_url, stock_syms, queue, is_crypto=False)))
        if crypto_syms:
            tasks.append(asyncio.create_task(self._ws_loop(self._ws_crypto_url, crypto_syms, queue, is_crypto=True)))

        try:
            while True:
                tick = await queue.get()
                yield tick
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def stream_bars(
        self,
        instruments: Sequence[Instrument],
        timeframe: BarTimeframe,
    ) -> AsyncIterator[Bar]:
        # Alpaca only streams 1-minute bars natively; for other timeframes the
        # caller should subscribe to ticks and run a TimeBarAggregator. We expose
        # the native 1m stream here and refuse other timeframes to avoid lying
        # about what we're delivering.
        if timeframe is not BarTimeframe.M1:
            raise ValueError(
                "alpaca only streams native 1m bars; use stream_ticks + TimeBarAggregator "
                f"for {timeframe.value}"
            )
        stock_syms = [i for i in instruments if i.asset_class in {AssetClass.EQUITY, AssetClass.ETF}]
        crypto_syms = [i for i in instruments if i.asset_class is AssetClass.CRYPTO]

        queue: asyncio.Queue[Bar] = asyncio.Queue(maxsize=10_000)
        tasks: list[asyncio.Task[None]] = []
        if stock_syms:
            tasks.append(asyncio.create_task(
                self._ws_loop(self._ws_stocks_url, stock_syms, queue, is_crypto=False, want_bars=True)
            ))
        if crypto_syms:
            tasks.append(asyncio.create_task(
                self._ws_loop(self._ws_crypto_url, crypto_syms, queue, is_crypto=True, want_bars=True)
            ))

        try:
            while True:
                bar = await queue.get()
                yield bar
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _ws_loop(
        self,
        url: str,
        instruments: Sequence[Instrument],
        queue: asyncio.Queue[Any],
        *,
        is_crypto: bool,
        want_bars: bool = False,
    ) -> None:
        """Run a WebSocket subscription with auto-reconnect and exponential backoff."""
        sym_to_inst = {i.symbol: i for i in instruments}
        symbols = [i.symbol for i in instruments]
        api_key = self._sp.get("alpaca_api_key")
        api_secret = self._sp.get("alpaca_api_secret")

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, max_size=2**22, ping_interval=20) as ws:
                    await ws.send(orjson.dumps({"action": "auth", "key": api_key, "secret": api_secret}).decode())
                    # Wait for auth ack
                    while True:
                        msg = await ws.recv()
                        for item in orjson.loads(msg):
                            if item.get("T") == "success" and item.get("msg") == "authenticated":
                                break
                            if item.get("T") == "error":
                                raise RuntimeError(f"alpaca ws auth error: {item}")
                        else:
                            continue
                        break

                    sub = {"action": "subscribe"}
                    if want_bars:
                        sub["bars"] = symbols
                    else:
                        sub["quotes"] = symbols
                        sub["trades"] = symbols
                    await ws.send(orjson.dumps(sub).decode())
                    log.info("alpaca_ws_subscribed", url=url, symbols=symbols, bars=want_bars)
                    backoff = 1.0  # reset after a successful subscribe

                    async for raw in ws:
                        for item in orjson.loads(raw):
                            t = item.get("T")
                            sym = item.get("S")
                            inst = sym_to_inst.get(sym)
                            if inst is None:
                                continue
                            if want_bars and t == "b":
                                await queue.put(_bar_from_alpaca_ws(inst, item))
                            elif not want_bars and t == "q":
                                await queue.put(_tick_from_alpaca_quote(inst, item))
                            elif not want_bars and t == "t":
                                await queue.put(_tick_from_alpaca_trade(inst, item))
            except (websockets.ConnectionClosed, OSError, RuntimeError) as exc:
                log.warning("alpaca_ws_reconnect", url=url, error=str(exc), backoff_s=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)


# ---- response decoders ----


def _bar_from_alpaca(inst: Instrument, tf: BarTimeframe, r: dict[str, Any]) -> Bar:
    return Bar(
        instrument=inst,
        timeframe=tf,
        timestamp=_parse_alpaca_ts(r["t"]),
        open=Decimal(str(r["o"])),
        high=Decimal(str(r["h"])),
        low=Decimal(str(r["l"])),
        close=Decimal(str(r["c"])),
        volume=Decimal(str(r["v"])),
        vwap=Decimal(str(r["vw"])) if r.get("vw") is not None else None,
        trade_count=int(r.get("n", 0)),
    )


def _bar_from_alpaca_ws(inst: Instrument, r: dict[str, Any]) -> Bar:
    return Bar(
        instrument=inst,
        timeframe=BarTimeframe.M1,
        timestamp=_parse_alpaca_ts(r["t"]),
        open=Decimal(str(r["o"])),
        high=Decimal(str(r["h"])),
        low=Decimal(str(r["l"])),
        close=Decimal(str(r["c"])),
        volume=Decimal(str(r["v"])),
        vwap=Decimal(str(r["vw"])) if r.get("vw") is not None else None,
        trade_count=int(r.get("n", 0)),
    )


def _tick_from_alpaca_quote(inst: Instrument, r: dict[str, Any]) -> Tick:
    return Tick(
        instrument=inst,
        timestamp=_parse_alpaca_ts(r["t"]),
        bid=Decimal(str(r["bp"])) if r.get("bp") is not None else None,
        ask=Decimal(str(r["ap"])) if r.get("ap") is not None else None,
        bid_size=Decimal(str(r["bs"])) if r.get("bs") is not None else None,
        ask_size=Decimal(str(r["as"])) if r.get("as") is not None else None,
        exchange=str(r["bx"]) if r.get("bx") is not None else None,
    )


def _tick_from_alpaca_trade(inst: Instrument, r: dict[str, Any]) -> Tick:
    return Tick(
        instrument=inst,
        timestamp=_parse_alpaca_ts(r["t"]),
        last_price=Decimal(str(r["p"])),
        last_size=Decimal(str(r["s"])),
        exchange=str(r["x"]) if r.get("x") is not None else None,
        conditions=tuple(r.get("c") or ()),
    )


def _parse_alpaca_ts(ts: str) -> Any:
    """Alpaca returns RFC3339 with ``Z`` suffix. Convert to aware UTC datetime."""
    from datetime import datetime

    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)
