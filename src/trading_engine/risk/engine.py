from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.trading_engine.broker.broker import Account
from src.trading_engine.data.data_feed import Instrument
from src.trading_engine.orders.order import OrderQuantity, OrderIntent, Intent


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade_pct: Decimal = Decimal('0.01')
    max_daily_loss_pct: Decimal = Decimal('0.03')
    max_account_drawdown_pct: Decimal = Decimal('0.10')
    max_position_notional_pct: Decimal = Decimal('0.20')
    max_open_positions: int = 10
    max_portfolio_exposure_pct: Decimal = Decimal('1.0')
    max_leverage: Decimal = Decimal('10')


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str = ""


@dataclass
class RiskEngine:
    limits: RiskLimits
    equity_peak: Decimal = Decimal('1e18')  # Sentinel for unset
    start_of_day_equity: Decimal = Decimal('1e18')  # Sentinel for unset

    def __post_init__(self):
        # Initialize equity_peak and start_of_day_equity if they are at their sentinel value
        # This assumes that these values will be set from actual account equity at some point.
        # For initial setup, they might be left as sentinel.
        pass

    def size_position(self, account: Account, intent: OrderIntent, instrument: Instrument) -> OrderQuantity:
        if intent.stop_distance <= Decimal('0'):
            return OrderQuantity(0)

        equity = account.get_current_equity()
        if equity <= Decimal('0'):
            return OrderQuantity(0)

        risk_amount = equity * self.limits.max_risk_per_trade_pct
        qty_unquantized = risk_amount / intent.stop_distance

        # Quantize to 0.0001 (assuming this is the minimum order size increment)
        qty_quantized = (qty_unquantized // Decimal('0.0001')) * Decimal('0.0001')

        # Cap by max_position_notional_pct
        if instrument.current_price and instrument.current_price > Decimal('0'):
            max_position_notional = equity * self.limits.max_position_notional_pct
            max_qty_by_notional = max_position_notional / instrument.current_price
            qty_quantized = min(qty_quantized, max_qty_by_notional)

        # Scale by confidence (assuming intent has a confidence attribute)
        # Assuming confidence is a Decimal between 0 and 1
        qty_scaled = qty_quantized * intent.confidence if hasattr(intent, 'confidence') else qty_quantized

        # Floor at zero
        final_qty = max(qty_scaled, Decimal('0'))
        return OrderQuantity(final_qty)

    def pre_trade_check(self, account: Account, intent: OrderIntent, qty: OrderQuantity, market_fresh: bool = True) -> RiskDecision:
        current_equity = account.get_current_equity()

        # Check for positive quantity
        if qty.value <= Decimal('0'):
            return RiskDecision(approved=False, reason="Order quantity must be positive.")

        # Check market data freshness
        if not market_fresh:
            return RiskDecision(approved=False, reason="Market data is not fresh.")

        # Check daily loss
        if self.start_of_day_equity < Decimal('1e17'):  # Check if start_of_day_equity has been set
            daily_loss = self.start_of_day_equity - current_equity
            if daily_loss / self.start_of_day_equity > self.limits.max_daily_loss_pct:
                return RiskDecision(approved=False, reason="Daily loss limit exceeded.")

        # Check account drawdown
        if self.equity_peak < Decimal('1e17'):  # Check if equity_peak has been set
            if current_equity < self.equity_peak:
                drawdown = self.equity_peak - current_equity
                if drawdown / self.equity_peak > self.limits.max_account_drawdown_pct:
                    return RiskDecision(approved=False, reason="Account drawdown limit exceeded.")
        
        # Check max open positions
        # This requires `account` to have a method to get open positions or similar.
        # Assuming `account.get_open_positions_count()` exists.
        if hasattr(account, 'get_open_positions_count') and account.get_open_positions_count() >= self.limits.max_open_positions:
            return RiskDecision(approved=False, reason="Maximum open positions limit exceeded.")

        # Check portfolio exposure
        # This requires `account` to have a method to get current exposure or similar.
        # Assuming `account.get_current_portfolio_exposure()` exists.
        if hasattr(account, 'get_current_portfolio_exposure'):
            proposed_exposure = account.get_current_portfolio_exposure() + (intent.instrument.current_price * qty.value) # Simplified calculation
            if proposed_exposure / current_equity > self.limits.max_portfolio_exposure_pct:
                return RiskDecision(approved=False, reason="Portfolio exposure limit exceeded.")

        # If all checks pass
        return RiskDecision(approved=True, reason="Risk checks passed.")

