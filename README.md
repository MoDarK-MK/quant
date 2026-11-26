# Trading Engine

Event-driven quantitative trading engine (Python). Implements the modular
architecture from the master engineering brief: market data backbone with
replay, real-time analytics, strategy layer, portfolio/risk engine, OMS with
an explicit order state machine and idempotent submission, paper execution,
deterministic backtesting and latency/audit monitoring.

## Layout

```
src/trading_engine/
  core/             event bus + typed event envelope
  domain/           instruments, quotes, intents, order state machine
  market_data/      backbone + deterministic CSV replay
  data_validation/  integrity checks, quarantine, quality report
  analytics/        causal feature engine + alpha synthesizer
  strategy/         strategy interface + momentum strategy
  risk/             sizing + pre-trade checks (drawdown, exposure, stale data)
  oms/              persistent OMS, idempotency, reconciliation
  execution/        paper broker + kill switch
  backtest/         event-driven backtester with Sharpe / drawdown metrics
  monitoring/       latency percentiles + audit trail
```

## Run tests

```
pip install -e ".[dev]"
pytest
```

Determinism note: the bus dispatches subscribers in registration order and
the paper broker fills at the quoted price plus fixed slippage, so repeated
backtests over the same data produce identical equity curves.