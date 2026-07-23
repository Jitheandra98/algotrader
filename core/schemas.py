"""Canonical schemas exchanged across every layer.

These are the only types data, features, strategies, risk, and execution agree on. Keep
them small, immutable where possible, and validated at construction. Pydantic v2 gives
us boundary validation; we use `Decimal` everywhere for money to avoid float drift.

Invariants enforced here cannot be loosened downstream:
- All timestamps are timezone-aware UTC. Naive timestamps raise.
- All monetary fields are `Decimal`. Floats raise via `model_config.strict`.
- Quantities are `Decimal` too — crypto and forex routinely use fractional shares.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------- Enums ----------


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"


class Direction(str, Enum):
    """Signal direction — what the strategy *wants* to happen."""

    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"
    HOLD = "HOLD"


class OrderSide(str, Enum):
    """Concrete order side — what the broker is told to do."""

    BUY = "BUY"
    SELL = "SELL"
    BUY_TO_COVER = "BUY_TO_COVER"
    SELL_SHORT = "SELL_SHORT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    MOC = "MOC"  # market on close
    LOC = "LOC"  # limit on close


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    OPG = "OPG"
    CLS = "CLS"


class OrderStatus(str, Enum):
    PENDING_NEW = "PENDING_NEW"  # accepted by us, not yet at broker
    NEW = "NEW"                  # acked by broker
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PENDING_CANCEL = "PENDING_CANCEL"    # cancel request in flight
    PENDING_REPLACE = "PENDING_REPLACE"  # modify request in flight
    REPLACED = "REPLACED"                # modify acked; old order is REPLACED


class BarTimeframe(str, Enum):
    """Supported bar timeframes. Tick aggregator targets are kept here so consumers
    don't have to re-parse strings."""

    S1 = "1s"
    S5 = "5s"
    S15 = "15s"
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


# ---------- Helpers ----------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_utc(value: datetime) -> datetime:
    """Reject naive datetimes; coerce non-UTC to UTC for consistency."""

    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware (UTC)")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        return value.astimezone(timezone.utc)
    return value


# Pydantic v2 will accept str/int/float and coerce to Decimal by default. We want strict
# typing for money fields — but we still need to *parse* JSON, where numbers may come
# as strings. The compromise: allow strings, reject floats that would lose precision.
PriceField = Annotated[Decimal, Field(ge=0, description="Decimal-typed price")]
SignedDecimal = Annotated[Decimal, Field(description="Decimal-typed signed quantity")]
PositiveDecimal = Annotated[Decimal, Field(gt=0, description="Decimal-typed positive value")]


class _StrictModel(BaseModel):
    """Common config for all schemas."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        ser_json_timedelta="iso8601",
    )


# ---------- Instrument ----------


class Instrument(_StrictModel):
    """Identifier for a tradable thing. Symbol + asset class + venue is enough for
    equities/ETFs/crypto. Options carry strike/expiry/right; futures carry expiry."""

    symbol: str = Field(min_length=1, max_length=32)
    asset_class: AssetClass
    venue: str | None = Field(default=None, description="MIC code or exchange id")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    # Option-specific
    strike: Decimal | None = None
    expiry: datetime | None = None
    option_right: str | None = Field(default=None, description="'C' or 'P'")
    # Future-specific
    contract_month: str | None = None

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, v: str) -> str:
        return v.upper()

    @field_validator("expiry")
    @classmethod
    def _validate_expiry(cls, v: datetime | None) -> datetime | None:
        return _require_utc(v) if v is not None else None

    @model_validator(mode="after")
    def _check_option_fields(self) -> Instrument:
        if self.asset_class is AssetClass.OPTION:
            if self.strike is None or self.expiry is None or self.option_right not in ("C", "P"):
                raise ValueError("options require strike, expiry, option_right ∈ {C,P}")
        return self

    @property
    def key(self) -> str:
        """Stable canonical key for use as a dict/Redis key."""
        if self.asset_class is AssetClass.OPTION:
            assert self.expiry is not None and self.strike is not None
            return (
                f"{self.symbol}|OPT|{self.expiry:%Y%m%d}|"
                f"{self.option_right}|{self.strike}"
            )
        if self.asset_class is AssetClass.FUTURE:
            return f"{self.symbol}|FUT|{self.contract_month}"
        return f"{self.symbol}|{self.asset_class.value}"


# ---------- Market data ----------


class Tick(_StrictModel):
    """Top-of-book + last trade snapshot. Either quote or trade fields may be missing
    depending on what the feed delivered, but the timestamp and instrument are always
    present."""

    instrument: Instrument
    timestamp: datetime
    bid: PriceField | None = None
    ask: PriceField | None = None
    bid_size: PositiveDecimal | None = None
    ask_size: PositiveDecimal | None = None
    last_price: PriceField | None = None
    last_size: PositiveDecimal | None = None
    exchange: str | None = None
    conditions: tuple[str, ...] = ()

    @field_validator("timestamp")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v)

    @model_validator(mode="after")
    def _at_least_one_side(self) -> Tick:
        if (
            self.bid is None
            and self.ask is None
            and self.last_price is None
        ):
            raise ValueError("tick must include at least bid, ask, or last_price")
        return self


class Bar(_StrictModel):
    """OHLCV bar at a given timeframe. ``timestamp`` is the bar's *open* time."""

    instrument: Instrument
    timeframe: BarTimeframe
    timestamp: datetime
    open: PriceField
    high: PriceField
    low: PriceField
    close: PriceField
    volume: Decimal = Field(ge=0)
    vwap: PriceField | None = None
    trade_count: int = Field(ge=0, default=0)

    @field_validator("timestamp")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v)

    @model_validator(mode="after")
    def _ohlc_sanity(self) -> Bar:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"OHLC ordering violated: O={self.open} H={self.high} L={self.low} C={self.close}")
        return self


# ---------- Signal ----------


class Signal(_StrictModel):
    """A strategy's intent. NOT an order — must pass through Risk before becoming one.

    Risk uses ``conviction`` (0..1) as one input to sizing; ``target`` and ``stop``
    are advisory and may be tightened by risk. ``rationale`` is required and is
    persisted with every trade for the journal.
    """

    id: UUID = Field(default_factory=uuid4)
    strategy_id: str = Field(min_length=1, max_length=64)
    instrument: Instrument
    direction: Direction
    conviction: float = Field(ge=0.0, le=1.0)
    target_price: PriceField | None = None
    stop_price: PriceField | None = None
    timeframe: BarTimeframe | None = None
    rationale: str = Field(min_length=1, max_length=2048)
    generated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generated_at")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v)


# ---------- Orders / fills / positions ----------


class Order(_StrictModel):
    """Order as submitted to the execution layer / broker. ``client_order_id`` is the
    idempotency key — re-submitting the same one must not double-fill."""

    id: UUID = Field(default_factory=uuid4)
    client_order_id: str = Field(min_length=1, max_length=64)
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: PositiveDecimal
    limit_price: PriceField | None = None
    stop_price: PriceField | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING_NEW
    parent_order_id: UUID | None = None
    strategy_id: str = Field(min_length=1, max_length=64)
    broker: str = Field(min_length=1, max_length=32)
    broker_order_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v)

    @model_validator(mode="after")
    def _price_required_when_appropriate(self) -> Order:
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LOC):
            if self.limit_price is None:
                raise ValueError(f"{self.order_type} requires limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP):
            if self.stop_price is None:
                raise ValueError(f"{self.order_type} requires stop_price")
        return self


class Fill(_StrictModel):
    """An execution report. One order may produce many fills (partial fills)."""

    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    instrument: Instrument
    side: OrderSide
    quantity: PositiveDecimal
    price: PriceField
    timestamp: datetime
    commission: Decimal = Field(ge=0, default=Decimal("0"))
    broker_fill_id: str | None = None
    liquidity: str | None = Field(default=None, description="'A'=added, 'R'=removed, 'X'=routed")

    @field_validator("timestamp")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v)


class Position(_StrictModel):
    """A net position in an instrument. Quantity is signed: positive = long,
    negative = short. ``average_price`` is the cost basis on the open side."""

    instrument: Instrument
    quantity: SignedDecimal
    average_price: PriceField
    current_price: PriceField | None = None
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    strategy_id: str | None = None
    opened_at: datetime = Field(default_factory=_utc_now)
    last_updated: datetime = Field(default_factory=_utc_now)

    @field_validator("opened_at", "last_updated")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v)

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def notional(self) -> Decimal:
        price = self.current_price if self.current_price is not None else self.average_price
        return abs(self.quantity) * price
