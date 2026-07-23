"""Alpaca live trading broker (paper or live).

Endpoints (as of 2026):
  REST paper:    https://paper-api.alpaca.markets
  REST live:     https://api.alpaca.markets
  WS  paper:     wss://paper-api.alpaca.markets/stream
  WS  live:      wss://api.alpaca.markets/stream

Auth via ``APCA-API-KEY-ID`` / ``APCA-API-SECRET-KEY`` (REST headers) and
``{"action": "auth", "key": "...", "secret": "..."}`` (WebSocket).

Default base URL is the *paper* endpoint. Set ``live=True`` to use real-money
trading — and you MUST also have ``LIVE=true`` set in the environment per the
guardrails in :mod:`core.config`. The broker checks both before connecting.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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
from core.schemas import (
    AssetClass,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from core.secrets import EnvSecretProvider, SecretProvider
from execution.base import (
    AccountInfo,
    BrokerAdapter,
    ExecutionEvent,
    ExecutionReport,
)

log = get_logger(__name__)


_PAPER_REST = "https://paper-api.alpaca.markets"
_LIVE_REST = "https://api.alpaca.markets"
_PAPER_WS = "wss://paper-api.alpaca.markets/stream"
_LIVE_WS = "wss://api.alpaca.markets/stream"


_SIDE_TO_ALPACA = {OrderSide.BUY: "buy", OrderSide.SELL: "sell"}
_ALPACA_TO_SIDE = {v: k for k, v in _SIDE_TO_ALPACA.items()}

_TYPE_TO_ALPACA = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
    OrderType.TRAILING_STOP: "trailing_stop",
    OrderType.MOC: "market",          # MOC maps to market with TIF=cls
    OrderType.LOC: "limit",
}

_TIF_TO_ALPACA = {
    TimeInForce.DAY: "day",
    TimeInForce.GTC: "gtc",
    TimeInForce.IOC: "ioc",
    TimeInForce.FOK: "fok",
    TimeInForce.OPG: "opg",
    TimeInForce.CLS: "cls",
}

_STATUS_FROM_ALPACA = {
    "new": OrderStatus.NEW,
    "accepted": OrderStatus.NEW,
    "pending_new": OrderStatus.PENDING_NEW,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.EXPIRED,
    "canceled": OrderStatus.CANCELED,
    "expired": OrderStatus.EXPIRED,
    "replaced": OrderStatus.REPLACED,
    "pending_cancel": OrderStatus.PENDING_CANCEL,
    "pending_replace": OrderStatus.PENDING_REPLACE,
    "rejected": OrderStatus.REJECTED,
    "suspended": OrderStatus.NEW,
    "calculated": OrderStatus.FILLED,
}

_EVENT_FROM_ALPACA = {
    "new": ExecutionEvent.NEW,
    "accepted": ExecutionEvent.ACK,
    "pending_new": ExecutionEvent.ACK,
    "partial_fill": ExecutionEvent.PARTIAL_FILL,
    "fill": ExecutionEvent.FILL,
    "canceled": ExecutionEvent.CANCEL,
    "expired": ExecutionEvent.EXPIRE,
    "replaced": ExecutionEvent.REPLACE,
    "rejected": ExecutionEvent.REJECT,
}


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"

    def __init__(
        self,
        *,
        live: bool = False,
        secret_provider: SecretProvider | None = None,
        request_timeout_s: float = 15.0,
    ) -> None:
        self._live = live
        self._rest_base = _LIVE_REST if live else _PAPER_REST
        self._ws_url = _LIVE_WS if live else _PAPER_WS
        self._sp = secret_provider or EnvSecretProvider()
        self._timeout = request_timeout_s
        self._http: httpx.AsyncClient | None = None

    # ---- lifecycle ----

    async def connect(self) -> None:
        if self._http is not None:
            return
        # Guardrail: real-money trading requires LIVE=true env on top of live=True ctor flag.
        if self._live and os.environ.get("LIVE", "").lower() != "true":
            raise RuntimeError(
                "AlpacaBroker(live=True) requires LIVE=true environment variable. "
                "Refusing to connect to live trading endpoint."
            )
        key = self._sp.get("alpaca_api_key")
        secret = self._sp.get("alpaca_api_secret")
        self._http = httpx.AsyncClient(
            base_url=self._rest_base,
            headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        log.info("alpaca_broker_connected", base=self._rest_base, live=self._live)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            log.info("alpaca_broker_closed")

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("AlpacaBroker not connected — call connect() first")
        return self._http

    # ---- submit / cancel / get ----

    async def submit(self, order: Order) -> Order:
        body = self._order_to_alpaca(order)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client().post("/v2/orders", content=orjson.dumps(body))
                # Idempotency: if the client_order_id is already in flight, Alpaca
                # returns 409. Fetch and return the existing.
                if resp.status_code == 409:
                    existing = await self._fetch_by_client_id(order.client_order_id)
                    if existing is not None:
                        return existing
                resp.raise_for_status()
        data = orjson.loads(resp.content)
        log.info(
            "alpaca_order_submitted",
            client_order_id=order.client_order_id,
            broker_order_id=data["id"],
            symbol=order.instrument.symbol,
            qty=str(order.quantity),
            side=order.side.value,
        )
        return self._alpaca_to_order(data, fallback=order)

    async def cancel(self, order_id: UUID) -> bool:
        # Map our internal UUID to broker_order_id via get_order. The caller can
        # also pass the broker_order_id by UUID-like coercion — for now we look
        # it up.
        existing = await self.get_order(order_id)
        if existing is None or existing.broker_order_id is None:
            return False
        resp = await self._client().delete(f"/v2/orders/{existing.broker_order_id}")
        # 204 = success; 422 = already filled/canceled.
        if resp.status_code in (204, 207):
            log.info("alpaca_order_canceled", broker_order_id=existing.broker_order_id)
            return True
        if resp.status_code == 422:
            log.info("alpaca_order_cancel_rejected", broker_order_id=existing.broker_order_id)
            return False
        resp.raise_for_status()
        return True

    async def get_order(self, order_id: UUID) -> Order | None:
        # Alpaca's `id` field is its own UUID — we may have stored ours separately.
        # For round-tripping we fetch by the broker's id if we have it; otherwise
        # fall back to client_order_id search.
        resp = await self._client().get(f"/v2/orders/{order_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._alpaca_to_order(orjson.loads(resp.content))

    async def _fetch_by_client_id(self, client_order_id: str) -> Order | None:
        resp = await self._client().get(
            "/v2/orders",
            params={"client_order_id": client_order_id, "status": "all", "limit": 1},
        )
        if resp.status_code != 200:
            return None
        items = orjson.loads(resp.content)
        if not items:
            return None
        return self._alpaca_to_order(items[0])

    # ---- account / positions ----

    async def positions(self) -> list[Position]:
        resp = await self._client().get("/v2/positions")
        resp.raise_for_status()
        items = orjson.loads(resp.content)
        return [self._alpaca_to_position(p) for p in items]

    async def account(self) -> AccountInfo:
        resp = await self._client().get("/v2/account")
        resp.raise_for_status()
        data = orjson.loads(resp.content)
        cash = Decimal(str(data["cash"]))
        equity = Decimal(str(data["equity"]))
        bp = Decimal(str(data["buying_power"]))
        return AccountInfo(
            cash=cash,
            equity=equity,
            buying_power=bp,
            is_margin_account=data.get("multiplier", "1") != "1",
            is_pdt_flagged=bool(data.get("pattern_day_trader", False)),
            is_pattern_day_trader=bool(data.get("pattern_day_trader", False)),
            daytrading_buying_power=(
                Decimal(str(data["daytrading_buying_power"]))
                if data.get("daytrading_buying_power") is not None
                else None
            ),
            multiplier=Decimal(str(data.get("multiplier", "1"))),
            currency=data.get("currency", "USD"),
            raw=data,
        )

    # ---- streaming ----

    async def stream_order_updates(self) -> AsyncIterator[ExecutionReport]:
        """Live ``trade_updates`` stream. Auto-reconnects with exponential backoff."""
        key = self._sp.get("alpaca_api_key")
        secret = self._sp.get("alpaca_api_secret")
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self._ws_url, max_size=2**22, ping_interval=20) as ws:
                    await ws.send(orjson.dumps({"action": "auth", "key": key, "secret": secret}).decode())
                    # Wait for auth ack
                    while True:
                        raw = await ws.recv()
                        m = orjson.loads(raw)
                        if m.get("stream") == "authorization":
                            if m.get("data", {}).get("status") != "authorized":
                                raise RuntimeError(f"alpaca ws auth failed: {m}")
                            break
                    await ws.send(
                        orjson.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}).decode()
                    )
                    log.info("alpaca_trade_updates_listening")
                    backoff = 1.0
                    async for raw in ws:
                        msg = orjson.loads(raw)
                        if msg.get("stream") != "trade_updates":
                            continue
                        report = self._trade_update_to_report(msg.get("data", {}))
                        if report is not None:
                            yield report
            except (websockets.ConnectionClosed, OSError, RuntimeError) as exc:
                log.warning("alpaca_ws_reconnect", error=str(exc), backoff_s=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ---- codecs ----

    def _order_to_alpaca(self, order: Order) -> dict[str, Any]:
        body: dict[str, Any] = {
            "client_order_id": order.client_order_id,
            "symbol": order.instrument.symbol,
            "qty": str(order.quantity),
            "side": _SIDE_TO_ALPACA[order.side],
            "type": _TYPE_TO_ALPACA.get(order.order_type, "market"),
            "time_in_force": _TIF_TO_ALPACA.get(order.time_in_force, "day"),
        }
        # Alpaca uses TIF=cls for MOC, TIF=opg for LOC at open; our schema's MOC
        # carries no TIF distinction so we map it explicitly.
        if order.order_type is OrderType.MOC:
            body["time_in_force"] = "cls"
        if order.order_type is OrderType.LOC:
            body["time_in_force"] = "opg"
        if order.limit_price is not None:
            body["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            body["stop_price"] = str(order.stop_price)
        # Crypto symbols use the slash form: BTC/USD. Equities don't.
        if order.instrument.asset_class is AssetClass.CRYPTO and "/" not in order.instrument.symbol:
            # Best-effort: if caller passed BTCUSD, leave it; if BTC, append /USD.
            if len(order.instrument.symbol) == 3:
                body["symbol"] = f"{order.instrument.symbol}/USD"
        return body

    def _alpaca_to_order(self, data: dict[str, Any], *, fallback: Order | None = None) -> Order:
        inst = (
            fallback.instrument
            if fallback is not None
            else self._instrument_from_alpaca(data)
        )
        side = _ALPACA_TO_SIDE[data["side"]]
        status_str = data.get("status", "new")
        status = _STATUS_FROM_ALPACA.get(status_str, OrderStatus.NEW)
        # Use the fallback's UUID if provided so the internal id is stable.
        internal_id = fallback.id if fallback is not None else UUID(data["id"])
        return Order(
            id=internal_id,
            client_order_id=data.get("client_order_id") or (fallback.client_order_id if fallback else "?"),
            instrument=inst,
            side=side,
            order_type=fallback.order_type if fallback else self._order_type_from_alpaca(data),
            quantity=Decimal(str(data.get("qty", "0"))),
            limit_price=Decimal(str(data["limit_price"])) if data.get("limit_price") else None,
            stop_price=Decimal(str(data["stop_price"])) if data.get("stop_price") else None,
            time_in_force=fallback.time_in_force if fallback else TimeInForce.DAY,
            status=status,
            strategy_id=fallback.strategy_id if fallback else "alpaca",
            broker=self.name,
            broker_order_id=data["id"],
        )

    def _trade_update_to_report(self, data: dict[str, Any]) -> ExecutionReport | None:
        evt_str = data.get("event")
        if evt_str not in _EVENT_FROM_ALPACA:
            return None
        order_data = data.get("order") or {}
        broker_oid = order_data.get("id")
        # We don't have the original internal UUID from the WS — best we can do
        # is parse the broker id as a UUID. The live engine maintains a map.
        try:
            internal_id = UUID(broker_oid) if broker_oid else uuid4()
        except (ValueError, TypeError):
            internal_id = uuid4()

        fill: Fill | None = None
        if evt_str in ("fill", "partial_fill"):
            ts = _parse_alpaca_ts(data.get("timestamp"))
            qty = Decimal(str(data.get("qty", "0")))
            price = Decimal(str(data.get("price", "0")))
            fill = Fill(
                order_id=internal_id,
                instrument=self._instrument_from_alpaca(order_data),
                side=_ALPACA_TO_SIDE[order_data["side"]] if order_data.get("side") else OrderSide.BUY,
                quantity=qty,
                price=price,
                timestamp=ts,
                broker_fill_id=str(data.get("execution_id") or data.get("id") or ""),
            )

        return ExecutionReport(
            event=_EVENT_FROM_ALPACA[evt_str],
            timestamp=_parse_alpaca_ts(data.get("timestamp")),
            order_id=internal_id,
            broker_order_id=broker_oid,
            status=_STATUS_FROM_ALPACA.get(order_data.get("status", "new")),
            fill=fill,
            leaves_qty=(
                Decimal(str(order_data["qty"])) - Decimal(str(order_data.get("filled_qty", "0")))
                if order_data.get("qty") is not None
                else None
            ),
            reject_reason=data.get("reason") if evt_str == "rejected" else None,
            raw=data,
        )

    def _alpaca_to_position(self, data: dict[str, Any]) -> Position:
        inst = self._instrument_from_alpaca(data)
        qty = Decimal(str(data["qty"]))
        avg = Decimal(str(data.get("avg_entry_price", "0")))
        current = Decimal(str(data["current_price"])) if data.get("current_price") else None
        unrealized = Decimal(str(data.get("unrealized_pl", "0")))
        return Position(
            instrument=inst,
            quantity=qty,
            average_price=avg,
            current_price=current,
            unrealized_pnl=unrealized,
        )

    @staticmethod
    def _instrument_from_alpaca(data: dict[str, Any]) -> Instrument:
        symbol = data.get("symbol") or data.get("asset_id", "?")
        cls = data.get("asset_class", "us_equity")
        if cls == "crypto":
            asset_class = AssetClass.CRYPTO
        elif cls in ("us_equity", "us_etf"):
            # Alpaca conflates etf into us_equity; we leave both as EQUITY here.
            asset_class = AssetClass.EQUITY
        else:
            asset_class = AssetClass.EQUITY
        return Instrument(symbol=symbol, asset_class=asset_class)

    @staticmethod
    def _order_type_from_alpaca(data: dict[str, Any]) -> OrderType:
        t = data.get("type", "market")
        if t == "market":
            return OrderType.MARKET
        if t == "limit":
            return OrderType.LIMIT
        if t == "stop":
            return OrderType.STOP
        if t == "stop_limit":
            return OrderType.STOP_LIMIT
        if t == "trailing_stop":
            return OrderType.TRAILING_STOP
        return OrderType.MARKET


def _parse_alpaca_ts(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)
