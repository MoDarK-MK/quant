"""Portfolio sizing and pre-trade risk engine (Module 05)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.models import Account, Instrument, Side, TradeIntent


@dataclass
class RiskLimits:
    max_risk_per_trade_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_account_drawdown_pct: float = 0.10
    max_position_notional_pct: float = 0.20
    max_open_positions: int = 10
    max_portfolio_exposure_pct: float = 1.0
    max_leverage: Decimal = Decimal(10)


@dataclass
class RiskDecision:
    approved: bool
    reason: str = ""


@dataclass
class RiskEngine:
    limits: RiskLimits = field(default_factory=RiskLimits)
    equity_peak: Decimal = field(default_factory=lambda: Decimal(0))
    start_of_day_equity: Decimal = field(default_factory=lambda: Decimal(0))

    def __post_init__(self) -> None:
        if self.equity_peak == 0:
            self.equity_peak = Decimal("1e18")  # effectively unset
        if self.start_of_day_equity == 0:
            self.start_of_day_equity = Decimal("1e18")  # unset

    def size_position(self, account: Account, intent: TradeIntent,
                      instrument: Instrument) -> Decimal:
        """Fixed-fractional, volatility-aware sizing in contract quantity."""
        equity = account.equity
        risk_amount = equity * Decimal(str(self.limits.max_risk_per_trade_pct))
        stop_distance = abs(intent.entry_ref - intent.stop_loss)
        if stop_distance <= 0:
            return Decimal(0)
        qty = (risk_amount / stop_distance).quantize(Decimal("0.0001"))
        # concentration cap
        max_notional = equity * Decimal(str(self.limits.max_position_notional_pct))
        if intent.entry_ref * qty > max_notional:
            qty = (max_notional / intent.entry_ref).quantize(Decimal("0.0001"))
        # confidence scaling (conservative)
        qty *= Decimal(str(intent.confidence))
        return max(Decimal(0), qty)

    def pre_trade_check(self, account: Account, intent: TradeIntent,
                        qty: Decimal, market_fresh: bool = True) -> RiskDecision:
        equity = account.equity
        # drawdown guard: only enforced once a peak has been recorded
        if self.equity_peak < Decimal("1e17") and self.equity_peak > 0:
            dd = (self.equity_peak - equity) / self.equity_peak
            if dd > Decimal(str(self.limits.max_account_drawdown_pct)):
                return RiskDecision(False, f"account drawdown {dd:.2%} exceeds limit")
        daily_loss = Decimal(0)
        if self.start_of_day_equity < Decimal("1e17") and self.start_of_day_equity > 0:
            daily_loss = (self.start_of_day_equity - equity) / self.start_of_day_equity
        if daily_loss > Decimal(str(self.limits.max_daily_loss_pct)):
            return RiskDecision(False, f"daily loss {daily_loss:.2%} exceeds limit")
        if not market_fresh:
            return RiskDecision(False, "market data is stale")
        if len(account.positions) >= self.limits.max_open_positions:
            return RiskDecision(False, "max open positions reached")
        exposure = sum(p.quantity * p.entry_price for p in account.positions.values())
        new_notional = qty * intent.entry_ref
        if intent.direction is Side.SELL:
            new_notional = -new_notional
        total_exposure = abs(exposure + new_notional)
        if total_exposure > equity * Decimal(str(self.limits.max_portfolio_exposure_pct)):
            return RiskDecision(False, "portfolio exposure limit exceeded")
        if qty <= 0:
            return RiskDecision(False, "non-positive quantity")
        return RiskDecision(True, "ok")