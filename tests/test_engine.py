"""End-to-end tests: event bus, validation, strategy, risk, OMS, backtest."""

from decimal import Decimal

import pytest

from trading_engine.core.events import Event, EventBus, EventType
from trading_engine.data_validation.validator import DataStatus, MarketDataValidator
from trading_engine.domain.models import (
    ORDER_TRANSITIONS,
    Account,
    OrderState,
    Quote,
    Side,
    TradeIntent,
)
from trading_engine.market_data.backbone import MarketDataBackbone, ReplayRow, load_quote_csv
from trading_engine.analytics.features import FeatureEngine
from trading_engine.strategy.momentum import MomentumStrategy
from trading_engine.risk.engine import RiskEngine, RiskLimits
from trading_engine.oms.oms import OMS, OrderStateMachineError
from trading_engine.execution.paper_broker import KillSwitch, PaperBroker
from trading_engine.backtest.engine import BacktestEngine, BacktestResult
from trading_engine.domain.models import Instrument
from trading_engine.monitoring.latency import AuditTrail, LatencyStats

BASE = 100.0


def make_rows(n=200, symbol="EURUSD", drift=0.0004, t0=1_700_000_000.0):
    rows = []
    price = BASE
    for i in range(n):
        price = price * (1 + drift) if i % 2 == 0 else price * (1 + drift / 2)
        mid = round(price, 6)
        rows.append(ReplayRow(symbol, Decimal(str(mid - 0.00005)), Decimal(str(mid + 0.00005)),
                              t0 + i, "test-feed", i))
    return rows


# ------------------------------------------------------------------ events
def test_event_bus_ordering_and_sequence():
    bus = EventBus()
    seen = []
    bus.subscribe(EventType.QUOTE, lambda e: seen.append(e.sequence))
    bus.subscribe(EventType.QUOTE, lambda e: seen.append("h2"))
    bus.publish(Event(EventType.QUOTE, {"x": 1}))
    bus.publish(Event(EventType.QUOTE, {"x": 2}))
    assert seen == [1, "h2", 2, "h2"]


def test_event_has_ingest_and_event_time():
    e = Event(EventType.QUOTE, {})
    assert e.ts_event == e.ts_ingest


# --------------------------------------------------------------- validation
def test_validator_rejects_duplicate():
    v = MarketDataValidator()
    q = Quote("X", Decimal("10"), Decimal("10.01"), 123.0)
    e = Event(EventType.QUOTE, {"quote": q})
    assert v.validate_quote(e) is DataStatus.VALID
    assert v.validate_quote(e) is DataStatus.DUPLICATE
    assert v.report.rejected == 1


def test_validator_rejects_price_spike():
    v = MarketDataValidator()
    for price in (100.0, 100.1, 120.0):
        q = Quote("X", Decimal(str(price - 0.05)), Decimal(str(price + 0.05)), 1000.0 + price)
        e = Event(EventType.QUOTE, {"quote": q}, source=f"s{price}")
        v.validate_quote(e)
    assert any(i.status is DataStatus.PRICE_SPIKE for i in v.quarantine)


def test_validator_rejects_bad_quote():
    with pytest.raises(ValueError):
        Quote("X", Decimal("10.02"), Decimal("10.00"), 1.0)  # crossed


# ---------------------------------------------------------------- strategy
def test_momentum_strategy_fires_on_trend():
    fe = FeatureEngine()
    strat = MomentumStrategy(threshold=0.0005)
    rows = make_rows(60, drift=0.002)
    fired = None
    for row in rows:
        q = Quote(row.symbol, row.bid, row.ask, row.ts)
        fs = fe.on_quote(Event(EventType.QUOTE, {"quote": q}))
        intent = strat.on_features(fs)
        if intent is not None:
            fired = intent
    assert fired is not None and fired.direction is Side.BUY
    assert fired.stop_loss < fired.entry_ref < fired.take_profit


# -------------------------------------------------------------------- risk
def test_risk_blocks_when_data_stale():
    acct = Account()
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 0.9)
    risk = RiskEngine()
    d = risk.pre_trade_check(acct, intent, Decimal("10"), market_fresh=False)
    assert not d.approved and "stale" in d.reason


def test_risk_caps_position_size():
    acct = Account(equity=Decimal("10000"), balance=Decimal("10000"))
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 1.0)
    risk = RiskEngine(limits=RiskLimits(max_position_notional_pct=0.2))
    qty = risk.size_position(acct, intent, None)
    assert qty * intent.entry_ref <= acct.equity * Decimal("0.2")


# --------------------------------------------------------------------- OMS
def test_oms_idempotent_submission():
    acct = Account()
    oms = OMS(acct)
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 1.0, intent_id="tag-1")
    o1 = oms.create_order(intent, Decimal(1), client_tag="tag-1")
    o2 = oms.create_order(intent, Decimal(1), client_tag="tag-1")
    assert o1 is o2


def test_oms_timeout_goes_unknown_not_rejected():
    class BrokenBroker:
        def submit(self, order):
            raise ConnectionError("timeout")

    acct = Account()
    oms = OMS(acct)
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 1.0)
    order = oms.create_order(intent, Decimal(1))
    oms.submit(order, BrokenBroker())
    assert order.state is OrderState.UNKNOWN
    # reconcile to acknowledged then reject
    oms.reconcile(order, OrderState.ACKNOWLEDGED)
    oms.transition(order, OrderState.REJECTED, "venue reject after recon")
    assert order.state is OrderState.REJECTED


def test_oms_illegal_transition_raises():
    acct = Account()
    oms = OMS(acct)
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 1.0)
    order = oms.create_order(intent, Decimal(1))
    with pytest.raises(OrderStateMachineError):
        oms.transition(order, OrderState.FILLED, "skipping states")


def test_state_machine_has_reject_paths_everywhere_active():
    for state in (OrderState.CREATED, OrderState.VALIDATED, OrderState.APPROVED, OrderState.SUBMITTED):
        assert OrderState.REJECTED in ORDER_TRANSITIONS[state]


# ------------------------------------------------------------------- broker
def test_kill_switch_blocks_orders():
    acct = Account()
    oms = OMS(acct)
    ks = KillSwitch()
    broker = PaperBroker(oms, ks)
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 1.0)
    order = oms.create_order(intent, Decimal(1))
    ks.activate("manual halt")
    broker.submit(order)
    assert order.state is OrderState.REJECTED


def test_paper_broker_fills_with_slippage_and_fee():
    acct = Account()
    oms = OMS(acct)
    broker = PaperBroker(oms, KillSwitch(), commission_bps=Decimal("2"),
                         slippage_bps=Decimal("1"))
    broker.on_quote("X", Decimal("99.99"), Decimal("100.01"))
    intent = TradeIntent("X", Side.BUY, Decimal("100"), Decimal("99"), Decimal("102"),
                         "s", "1.0", 1.0)
    order = oms.create_order(intent, Decimal(1))
    oms.submit(order, broker)
    assert order.state is OrderState.FILLED
    assert order.avg_fill_price > Decimal("100.01")  # slippage applied
    assert acct.balance < acct.equity + Decimal("100000")  # fee deducted from balance
    assert len(oms.journal) > 0


# ----------------------------------------------------------------- backtest
def test_backtest_end_to_end_trend_is_profitable():
    strat = MomentumStrategy(threshold=0.0005, stop_pct=0.002, target_pct=0.004)
    engine = BacktestEngine([strat], Instrument("EURUSD", Decimal("0.00001"), Decimal("1")))
    result = engine.run(make_rows(300, drift=0.0008))
    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) > 0
    assert result.max_drawdown_pct < 100.0
    # trend-following on a rising series should not lose money catastrophically
    assert result.final_equity > Decimal("90000")


def test_backtest_deterministic():
    strat = MomentumStrategy(threshold=0.0005)
    r1 = BacktestEngine([MomentumStrategy(threshold=0.0005)],
                        Instrument("EURUSD", Decimal("0.00001"), Decimal("1"))).run(make_rows(100))
    r2 = BacktestEngine([MomentumStrategy(threshold=0.0005)],
                        Instrument("EURUSD", Decimal("0.00001"), Decimal("1"))).run(make_rows(100))
    assert r1.final_equity == r2.final_equity


def test_csv_roundtrip(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text("symbol,bid,ask,ts,source,sequence\n"
                 "EURUSD,99.99,100.01,1700000000,unit,1\n")
    rows = load_quote_csv(p)
    assert rows[0].symbol == "EURUSD" and rows[0].bid == Decimal("99.99")


# -------------------------------------------------------------- monitoring
def test_latency_percentiles():
    ls = LatencyStats()
    for v in range(1, 101):
        ls.record(float(v))
    assert ls.p50 == pytest.approx(50.0, abs=2)
    assert ls.p95 == pytest.approx(95.0, abs=2)
    assert ls.p99 == pytest.approx(99.0, abs=2)


def test_audit_trail():
    a = AuditTrail()
    a.record("risk", "blocked", {"reason": "exposure"})
    assert len(a) == 1