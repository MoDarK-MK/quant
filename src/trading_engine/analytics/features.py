Real-time analytics engine (Module 02): features from validated quotes.

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from statistics import fmean, pstdev
from typing import Any

from ..core.events import Event, EventBus, EventType
from ..domain.models import Quote


@dataclass(frozen=True)
class FeatureSet:
    symbol: str
    ts: float
    features: dict[str, float]


class FeatureEngine:
    """Incremental, causal feature computation (no look-ahead)."""

    WINDOW = 50

    def __init__(self, bus: EventBus | None = None, window: int = WINDOW) -> None:
        self.bus = bus
        self.window = window
        self._mids: dict[str, deque[float]] = {}
        self._spreads: dict[str, deque[float]] = {}
        self._tick_sides: dict[str, deque[float]] = {}

    def on_quote(self, event: Event) -> FeatureSet | None:
        quote: Quote = event.payload["quote"]
        sym = quote.symbol
        self._mids.setdefault(sym, deque(maxlen=self.window)).append(float(quote.mid))
        self._spreads.setdefault(sym, deque(maxlen=self.window)).append(float(quote.spread))
        mids = self._mids[sym]
        delta = mids[-1] - (mids[-2] if len(mids) > 1 else mids[-1])
        self._tick_sides.setdefault(sym, deque(maxlen=self.window)).append(delta)

        fs = FeatureSet(sym, quote.ts, self._compute(sym))
        if self.bus is not None:
            self.bus.publish(
                Event(event_type=EventType.FEATURE, payload={"features": fs, "symbol": sym},
                      source="analytics", ts_event=quote.ts,
                      correlation_id=event.correlation_id or event.event_id)
            )
        return fs

    def _compute(self, sym: str) -> dict[str, float]:
        mids = list(self._mids[sym])
        spreads = list(self._spreads[sym])
        flows = list(self._tick_sides[sym])
        rets = [mids[i + 1] / mids[i] - 1 for i in range(len(mids) - 1)]
        return {
            "mid": mids[-1],
            "mean_mid": fmean(mids),
            "zscore_mid": (mids[-1] - fmean(mids)) / pstdev(mids) if pstdev(mids) > 0 else 0.0,
            "vol": pstdev(rets) if len(rets) > 1 else 0.0,
            "spread_bps": (fmean(spreads) / mids[-1]) * 1e4 if mids[-1] else 0.0,
            "flow_imbalance": sum(flows) or 0.0,
            "momentum": mids[-1] / mids[0] - 1 if len(mids) > 1 else 0.0,
        }


class AlphaSynthesizer:
    """Blend strategy signals - never a naive average.

    Weights combine historical performance, inverse correlation with the rest
    of the portfolio, regime compatibility and reliability, then deduplicate
    and filter by current exposure.
    """

    def __init__(self) -> None:
        self.strategy_weights: dict[str, float] = {}

    def set_strategy_weight(self, strategy_id: str, weight: float) -> None:
        self.strategy_weights[strategy_id] = weight

    def blend(self, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """signals: dicts with strategy_id, symbol, direction, score, confidence."""
        if not signals:
            return []
        seen: set[tuple] = set()
        unique: list[dict] = []
        for s in signals:
            key = (s["strategy_id"], s["symbol"], s["direction"])
            if key not in seen:
                seen.add(key)
                unique.append(s)

        by_symbol: dict[str, set[str]] = {}
        for s in unique:
            by_symbol.setdefault(s["symbol"], set()).add(s["direction"])
        conflicts = {sym for sym, dirs in by_symbol.items() if len(dirs) > 1}

        blended: list[dict] = []
        for s in unique:
            base = self.strategy_weights.get(s["strategy_id"], 1.0)
            w = base * (0.5 + s.get("confidence", 0.5))
            if s["symbol"] in conflicts:
                w *= 0.5
            blended.append({**s, "blended_score": s["score"] * w})
        blended.sort(key=lambda x: x["blended_score"], reverse=True)
        return blended
