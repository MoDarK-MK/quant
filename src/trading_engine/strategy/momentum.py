"""Strategy interface and a concrete momentum strategy (Module 04)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..analytics.features import FeatureSet
from ..domain.models import Side, TradeIntent


class Strategy(ABC):
    strategy_id: str = "base"
    version: str = "1.0.0"

    @abstractmethod
    def on_features(self, fs: FeatureSet) -> TradeIntent | None:
        """Return a TradeIntent recommendation, or None."""


class MomentumStrategy(Strategy):
    """Enter when the short-window momentum exceeds a threshold.

    Confidence grows with signal strength; stop/TP are ATR-proxy scaled.
    """

    def __init__(self, threshold: float = 0.0015, stop_pct: float = 0.002,
                 target_pct: float = 0.004) -> None:
        self.threshold = threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.last_direction: Side | None = None

    @property
    def strategy_id(self) -> str:  # noqa: D102
        return "momentum"

    def on_features(self, fs: FeatureSet) -> TradeIntent | None:
        mom = fs.features.get("momentum", 0.0)
        if abs(mom) < self.threshold:
            return None
        direction = Side.BUY if mom > 0 else Side.SELL
        # avoid firing the same direction repeatedly without a flip
        if direction is self.last_direction:
            return None
        self.last_direction = direction

        entry = fs.features["mid"]
        if direction is Side.BUY:
            sl, tp = entry * (1 - self.stop_pct), entry * (1 + self.target_pct)
        else:
            sl, tp = entry * (1 + self.stop_pct), entry * (1 - self.target_pct)
        conf = min(1.0, abs(mom) / self.threshold / 2)
        return TradeIntent(
            symbol=fs.symbol,
            direction=direction,
            entry_ref=__import__("decimal").Decimal(str(entry)),
            stop_loss=__import__("decimal").Decimal(str(round(sl, 8))),
            take_profit=__import__("decimal").Decimal(str(round(tp, 8))),
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            confidence=round(conf, 4),
            supporting_features=("momentum",),
            ts=fs.ts,
        )
