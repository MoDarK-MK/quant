"""Demo: run a momentum backtest on synthetic trending data.

Usage: python -m trading_engine.demo
"""

import time
from decimal import Decimal

from .backtest.engine import BacktestEngine
from .domain.models import Instrument
from .market_data.backbone import ReplayRow
from .strategy.momentum import MomentumStrategy

T0 = 1_700_000_000.0


def make_rows(n=300, symbol="EURUSD", drift=0.0008, base=100.0):
    rows, price = [], base
    for i in range(n):
        price *= 1 + drift if i % 2 == 0 else 1 + drift / 2
        mid = round(price, 6)
        rows.append(ReplayRow(symbol, Decimal(str(mid - 0.00005)),
                              Decimal(str(mid + 0.00005)), T0 + i, "demo", i))
    return rows


def main() -> None:
    strat = MomentumStrategy(threshold=0.0005)
    engine = BacktestEngine([strat], Instrument("EURUSD", Decimal("0.00001"), Decimal("1")))
    result = engine.run(make_rows())
    print(f"final_equity={result.final_equity}")
    print(f"trades={len(result.trades)}")
    print(f"sharpe={result.sharpe:.2f}")
    print(f"max_drawdown_pct={result.max_drawdown_pct:.2f}")


if __name__ == "__main__":
    main()