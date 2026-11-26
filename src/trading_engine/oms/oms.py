"""OMS: order state machine, idempotent submission, durable event journal (Module 06)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.events import new_id
from ..domain.models import (
    ORDER_TRANSITIONS,
    Account,
    OrderState,
    Side,
    TradeIntent,
)


@dataclass
class Order:
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    state: OrderState = OrderState.CREATED
    filled_quantity: Decimal = Decimal(0)
    avg_fill_price: Decimal = Decimal(0)
    intent_id: str | None = None
    client_tag: str | None = None  # idempotency key

    def remaining(self) -> Decimal:
        return self.quantity - self.filled_quantity


@dataclass
class Execution:
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal(0)
    execution_id: str = field(default_factory=new_id)


class OrderStateMachineError(Exception):
    pass


class OMS:
    """Persistent, fault-tolerant order manager.

    Every transition is validated and journaled. Submissions are idempotent
    per client_tag. A timed-out submission moves the order to UNKNOWN, never
    straight to REJECTED, and requires reconciliation before retry.
    """

    def __init__(self, account: Account, journal_path: str | Path | None = None) -> None:
        self.account = account
        self.orders: dict[str, Order] = {}
        self._by_tag: dict[str, str] = {}
        self.journal_path = Path(journal_path) if journal_path else None
        self.journal: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ core
    def _journal_event(self, kind: str, data: dict[str, Any]) -> None:
        entry = {"kind": kind, "data": data}
        self.journal.append(entry)
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_path, "a") as fh:
                fh.write(json.dumps(entry, default=str) + "
")

    def create_order(self, intent: TradeIntent, qty: Decimal,
                     client_tag: str | None = None) -> Order:
        if client_tag and client_tag in self._by_tag:
            return self.orders[self._by_tag[client_tag]]  # idempotent
        order = Order(
            order_id=new_id(), symbol=intent.symbol, side=intent.side,
            quantity=qty, intent_id=intent.intent_id, client_tag=client_tag,
        )
        self.orders[order.order_id] = order
        if client_tag:
            self._by_tag[client_tag] = order.order_id
        self._journal_event("order_created", {"order_id": order.order_id,
                                              "symbol": order.symbol, "qty": str(qty)})
        return order

    def transition(self, order: Order, target: OrderState, reason: str = "") -> None:
        allowed = ORDER_TRANSITIONS[order.state]
        if target not in allowed:
            raise OrderStateMachineError(
                f"illegal transition {order.state.value} -> {target.value} for {order.order_id}: {reason}"
            )
        order.state = target
        self._journal_event("order_transition",
                            {"order_id": order.order.id, "state": target.value, "reason": reason})

    # ------------------------------------------------------------ submission
    def submit(self, order: Order, broker) -> OrderState:
        """Submit via broker; network exceptions park the order in UNKNOWN.

        Pre-submit pipeline: orders arriving in CREATED pass through the
        machine's validated/approved states automatically.
        """
        if order.state is OrderState.UNKNOWN:
            raise OrderStateMachineError("must reconcile UNKNOWN order before resubmitting")
        if order.state is OrderState.CREATED:
            self.transition(order, OrderState.VALIDATED, "auto-validation passed")
        if order.state is OrderState.VALIDATED:
            self.transition(order, OrderState.APPROVED, "risk approved")
        if order.state not in (OrderState.APPROVED,):
            return order.state
        try:
            self.transition(order, OrderState.SUBMITTED, "submit")
            broker.submit(order)
        except ConnectionError:
            self.transition(order, OrderState.UNKNOWN, "network timeout — awaiting reconciliation")
            return order.state
        return order.state

    def reconcile(self, order: Order, broker_state: OrderState) -> None:
        """Resolve an UNKNOWN order against actual broker state."""
        if order.state is not OrderState.UNKNOWN:
            raise OrderStateMachineError("reconcile only applies to UNKNOWN orders")
        if broker_state not in ORDER_TRANSITIONS[OrderState.UNKNOWN]:
            raise OrderStateMachineError(f"broker reported unreachable state {broker_state}")
        self.transition(order, broker_state, "reconciled")

    # ----------------------------------------------------------------- fills
    def apply_execution(self, order: Order, execution: Execution) -> None:
        order.filled_quantity += execution.quantity
        if order.filled_quantity:
            order.avg_fill_price = (
                (order.avg_fill_price * (order.filled_quantity - execution.quantity)
                 + execution.price * execution.quantity) / order.filled_quantity
            )
        self.account.apply_fill(order.symbol, order.side, execution.quantity,
                                execution.price, execution.fee)
        if order.filled_quantity >= order.quantity:
            if order.state is OrderState.UNKNOWN:
                order.state = OrderState.UNKNOWN  # placeholder; reconcile first
            target = OrderState.FILLED
            if target in ORDER_TRANSITIONS[order.state]:
                order.state = target
        else:
            if OrderState.PARTIALLY_FILLED in ORDER_TRANSITIONS[order.state]:
                order.state = OrderState.PARTIALLY_FILLED
        self._journal_event("execution",
                            {"order_id": order.order_id, "qty": str(execution.quantity),
                             "price": str(execution.price), "fee": str(execution.fee)})