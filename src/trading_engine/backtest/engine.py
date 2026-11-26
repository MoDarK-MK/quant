"""Backtest engine (Module 09)."""
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import fmean, pstdev
from ..analytics.features import FeatureEngine
from ..core.events import EventBus, EventType
from ..domain.models import Account, Instrument, Quote, Side
from ..execution.paper_broker import KillSwitch, PaperBroker
from ..market_data.backbone import MarketDataBackbone, ReplayRow
from ..oms.oms import OMS
from ..risk.engine import RiskEngine
@dataclass
class TradeRecord:
    symbol: str
    side: str
    quantity: str
    entry: str
    exit: str | None = None
    pnl: str | None = None
@dataclass
class BacktestResult:
    final_equity: Decimal
    pnl: Decimal
    trades: list = field(default_factory=list)
    rejected_signals: int = 0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    equity_curve: list = field(default_factory=list)
class BacktestEngine:
    def __init__(self, strategies, instrument: Instrument, starting_equity: Decimal = Decimal("100000")) -> None:
        self.bus = EventBus()
        self.account = Account(equity=starting_equity, balance=starting_equity)
        self.oms = OMS(self.account)
        self.kill_switch = KillSwitch()
        self.broker = PaperBroker(self.oms, self.kill_switch)
        self.risk = RiskEngine()
        self.backbone = MarketDataBackbone(self.bus)
        self.features = FeatureEngine(self.bus)
        self.strategies = strategies
        self.instrument = instrument
        self.trades = []
        self.rejected = 0
        self._equity_curve = []
        self._latest_quote = {}
        self.bus.subscribe(EventType.QUOTE, self._on_quote)
        self.bus.subscribe(EventType.FEATURE, self._on_feature)
    def _on_quote(self, event) -> None:
        quote = event.payload["quote"]
        self._latest_quote[quote.symbol] = quote
        self.broker.on_quote(quote.symbol, quote.bid, quote.ask)
        self._equity_curve.append(float(self.account.equity))
        self._manage_exits(quote)
    def _on_feature(self, event) -> None:
        fs = event.payload["features"]
        for strat in self.strategies:
            intent = strat.on_features(fs)
            if intent is None:
                continue
            qty = self.risk.size_position(self.account, intent, self.instrument)
            decision = self.risk.pre_trade_check(self.account, intent, qty)
            if not decision.approved:
                self.rejected += 1
                continue
            order = self.oms.create_order(intent, qty, client_tag=intent.intent_id)
            self.oms.submit(order, self.broker)
    def _manage_exits(self, quote: Quote) -> None:
        pos = self.account.positions.get(quote.symbol)
        if pos is None:
            return
        mark = quote.mid
        hit_stop = (pos.side is Side.BUY and mark <= pos.entry_price * Decimal("0.998")) or (pos.side is Side.SELL and mark >= pos.entry_price * Decimal("1.002"))
        if hit_stop:
            entry = pos.entry_price
            pnl = pos.unrealized_pnl(mark)
            self.oms.account.apply_fill(quote.symbol, Side.SELL if pos.side is Side.BUY else Side.BUY, pos.quantity, mark)
            self.trades.append(TradeRecord(quote.symbol, pos.side.value, str(pos.quantity), str(entry), str(mark), str(pnl.quantize(Decimal("0.01")))))
    def run(self, rows) -> BacktestResult:
        for row in rows:
            quote = Quote(row.symbol, row.bid, row.ask, row.ts, row.source, row.sequence)
            self.backbone.publish_quote(quote)
        curve = self._equity_curve or [float(self.account.equity)]
        final = self.account.equity
        return BacktestResult(final_equity=final, pnl=final - Decimal("100000"), trades=self.trades, rejected_signals=self.rejected, sharpe=self._sharpe(curve), max_drawdown_pct=self._max_drawdown(curve), equity_curve=curve)
    @staticmethod
    def _sharpe(curve):
        rets = [curve[i + 1] / curve[i] - 1 for i in range(len(curve) - 1)]
        if len(rets) < 2 or pstdev(rets) == 0:
            return 0.0
        return (fmean(rets) / pstdev(rets)) * (len(rets) ** 0.5)
    @staticmethod
    def _max_drawdown(curve):
        peak = curve[0]
        mdd = 0.0
        for v in curve:
            peak = max(peak, v)
            if peak > 0:
                mdd = max(mdd, (peak - v) / peak)
        return mdd * 100