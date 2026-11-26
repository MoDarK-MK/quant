"""Paper broker and kill-switch."""
from decimal import Decimal
from ..domain.models import OrderState
from ..oms.oms import Execution, OMS
class KillSwitch:
    def __init__(self) -> None:
        self._active = False
        self.reason: str | None = None
    @property
    def active(self) -> bool:
        return self._active
    def activate(self, reason: str) -> None:
        self._active = True
        self.reason = reason
    def deactivate(self, reason: str = "manual reset") -> None:
        self._active = False
        self.reason = None
class PaperBroker:
    def __init__(self, oms: OMS, kill_switch: KillSwitch, commission_bps: Decimal = Decimal("2"), slippage_bps: Decimal = Decimal("0.5")) -> None:
        self.oms = oms
        self.kill_switch = kill_switch
        self.commission_bps = commission_bps
        self.slippage_bps = slippage_bps
        self._last_bid = None
        self._last_ask = None
        self._last_symbol = None
    def on_quote(self, symbol: str, bid: Decimal, ask: Decimal) -> None:
        self._last_bid, self._last_ask = bid, ask
        self._last_symbol = symbol
    def submit(self, order) -> None:
        if self.kill_switch.active:
            self.oms.transition(order, OrderState.REJECTED, f"kill-switch active: {self.kill_switch.reason}")
            return
        bid = self._last_bid
        ask = self._last_ask
        if bid is None or ask is None:
            self.oms.transition(order, OrderState.REJECTED, "no market data")
            return
        self.oms.transition(order, OrderState.ACKNOWLEDGED, "paper ack")
        slip = Decimal(str(self.slippage_bps)) / Decimal(10000)
        if order.side.value == "buy":
            price = ask * (1 + slip)
        else:
            price = bid * (1 - slip)
        fee = price * order.quantity * Decimal(str(self.commission_bps)) / Decimal(10000)
        execution = Execution(order.order_id, order.symbol, order.side, order.quantity, price, fee)
        self.oms.apply_execution(order, execution)