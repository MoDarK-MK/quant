"""Core trading domain models (typed, immutable where possible)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderState(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


ORDER_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.CREATED: {OrderState.VALIDATED, OrderState.REJECTED},
    OrderState.VALIDATED: {OrderState.APPROVED, OrderState.REJECTED},
    OrderState.APPROVED: {OrderState.SUBMITTED, OrderState.REJECTED},
    OrderState.SUBMITTED: {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.UNKNOWN},
    OrderState.ACKNOWLEDGED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                              OrderState.CANCEL_PENDING, OrderState.REJECTED},
    OrderState.PARTIALLY_FILLED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                  OrderState.CANCEL_PENDING},
    OrderState.CANCEL_PENDING: {OrderState.CANCELLED, OrderState.FILLED, OrderState.PARTIALLY_FILLED},
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.EXPIRED: set(),
    OrderState.UNKNOWN: {OrderState.ACKNOWLEDGED, OrderState.REJECTED},
}


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick_size: Decimal
    tick_value: Decimal
    contract_size: Decimal = Decimal(1)
    max_leverage: Decimal = Decimal(100)

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    ts: float
    source: str = "unknown"
    sequence: int | None = None

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError(f"invalid quote bid={self.bid} ask={self.ask}")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True)
class TradeTick:
    symbol: str
    price: Decimal
    size: Decimal
    aggressor: Side
    ts: float
    source: str = "unknown"
    sequence: int | None = None


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    direction: Side
    entry_ref: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    strategy_id: str
    strategy_version: str
    confidence: float
    expected_holding_period: str = "unspecified"
    supporting_features: tuple[str, ...] = ()
    intent_id: str = ""
    ts: float = 0.0

    def __post_init__(self) -> None:
        import time as _t

        if not self.intent_id:
            object.__setattr__(self, "intent_id", __import__("uuid").uuid4().hex)
        if not self.ts:
            object.__setattr__(self, "ts", self.ts or _t.time())
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.direction is Side.BUY and self.stop_loss >= self.entry_ref:
            raise ValueError("long stop must be below entry")
        if self.direction is Side.SELL and self.stop_loss <= self.entry_ref:
            raise ValueError("short stop must be above entry")

    @property
    def side(self) -> Side:
        return self.direction


@dataclass
class Position:
    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        diff = (mark - self.entry_price) if self.side is Side.BUY else (self.entry_price - mark)
        return diff * self.quantity


@dataclass
class Account:
    account_id: str = "paper-1"
    equity: Decimal = field(default_factory=lambda: Decimal("100000"))
    balance: Decimal = field(default_factory=lambda: Decimal("100000"))
    positions: dict[str, Position] = field(default_factory=dict)

    def apply_fill(self, symbol: str, side: Side, quantity: Decimal, price: Decimal,
                   fee: Decimal = Decimal(0)) -> None:
        pos = self.positions.get(symbol)
        if pos is None or pos.side is not side and pos.quantity == 0:
            self.positions[symbol] = Position(symbol, side, quantity, price)
        elif pos.side is side:
            total = pos.quantity + quantity
            pos.entry_price = ((pos.entry_price * pos.quantity) + (price * quantity)) / total
            pos.quantity = total
        else:
            if quantity < pos.quantity:
                pos.quantity -= quantity
            elif quantity == pos.quantity:
                del self.positions[symbol]
            else:
                remaining = quantity - pos.quantity
                self.positions[symbol] = Position(symbol, side, remaining, price)
        self.balance -= fee
        self.equity = self.balance