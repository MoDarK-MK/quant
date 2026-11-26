from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Literal

from pydantic import BaseModel, Field


class OrderSide(Enum):
    BUY = auto()
    SELL = auto()


class OrderType(Enum):
    LIMIT = auto()
    MARKET = auto()


class OrderStatus(Enum):
    PENDING = auto()
    OPEN = auto()
    FILLED = auto()
    CANCELED = auto()
    REJECTED = auto()


class PositionSide(Enum):
    LONG = auto()
    SHORT = auto()
    FLAT = auto()


class Account(BaseModel):
    id: str
    exchange_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class Balance(BaseModel):
    account_id: str
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal
    updated_at: datetime


class Fill(BaseModel):
    id: str
    order_id: str
    exchange_trade_id: str | None
    price: Decimal
    qty: Decimal
    commission: Decimal
    commission_asset: str
    created_at: datetime


class Order(BaseModel):
    id: str
    account_id: str
    symbol: str
    side: OrderSide
    type: OrderType
    price: Decimal | None
    qty: Decimal
    reduce_only: bool
    post_only: bool
    status: OrderStatus
    filled_qty: Decimal = Field(default=Decimal("0"))
    filled_price: Decimal = Field(default=Decimal("0"))
    created_at: datetime
    updated_at: datetime
    fills: list[Fill] = Field(default_factory=list)

    def is_closed(self) -> bool:
        return self.status in [
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        ]

    def is_active(self) -> bool:
        return self.status in [OrderStatus.PENDING, OrderStatus.OPEN]

    def get_pending_qty(self) -> Decimal:
        return self.qty - self.filled_qty


class Position(BaseModel):
    id: str
    account_id: str
    symbol: str
    side: PositionSide
    unrealized_pnl: Decimal
    roe: Decimal
    leverage: Decimal
    isolated: bool
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Decimal
    pnl_source: Literal["realized", "unrealized"]
    buying_power: Decimal
    notional: Decimal
    bid_price: Decimal
    ask_price: Decimal
    qty: Decimal
    updated_at: datetime


class Candle(BaseModel):
    symbol: str
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: datetime


class Ticker(BaseModel):
    symbol: str
    price: Decimal
    open_time: datetime


class Trade(BaseModel):
    id: str
    symbol: str
    price: Decimal
    qty: Decimal
    taker_side: OrderSide
    open_time: datetime