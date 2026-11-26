'''Market data validation and integrity engine (Module 03).

Invalid or suspicious events are quarantined and never silently forwarded.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..core.events import Event
from ..domain.models import Quote


class DataStatus(str, Enum):
    VALID = "valid"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    PRICE_SPIKE = "price_spike"
    STALE = "stale"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INCONSISTENT = "inconsistent"


@dataclass
class QuarantinedItem:
    event_id: str
    status: DataStatus
    reason: str
    payload: dict[str, Any]


@dataclass
class DataQualityReport:
    accepted: int = 0
    rejected: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    def record(self, status: DataStatus) -> None:
        if status is DataStatus.VALID:
            self.accepted += 1
        else:
            self.rejected += 1
            self.counts[status.value] = self.counts.get(status.value, 0) + 1


class MarketDataValidator:
    """Deterministic, stateful validation of quote events."""

    def __init__(
        self,
        price_deviation_pct: float = 0.05,
        staleness_seconds: float = 30.0,
        max_backwards_skew: float = 5.0,
    ) -> None:
        self.price_deviation_pct = price_deviation_pct
        self.staleness_seconds = staleness_seconds
        self.max_backwards_skew = max_backwards_skew
        self._last_ts: dict[str, float] = {}
        self._last_mid: dict[str, float] = {}
        self._seen_ids: set[str] = set()
        self.quarantine: list[QuarantinedItem] = []
        self.report = DataQualityReport()

    def validate_quote(self, event: Event) -> DataStatus:
        quote = event.payload.get("quote")
        if isinstance(quote, Quote):
            symbol, bid, ask, ts = quote.symbol, float(quote.bid), float(quote.ask), quote.ts
        else:
            symbol = event.payload["symbol"]
            bid, ask, ts = event.payload["bid"], event.payload["ask"], event.payload["ts"]

        if event.event_id in self._seen_ids:
            return self._reject(event, DataStatus.DUPLICATE, "duplicate event id")
        if ts <= 0 or ts > self._now() + 60:
            return self._reject(event, DataStatus.INVALID_TIMESTAMP, f"timestamp {ts} out of range")

        last_ts = self._last_ts.get(symbol)
        if last_ts is not None and ts < last_ts - self.max_backwards_skew:
            return self._reject(event, DataStatus.OUT_OF_ORDER, "event older than skew allowance")

        mid = (bid + ask) / 2
        prev = self._last_mid.get(symbol)
        if prev is not None and prev > 0:
            dev = abs(mid - prev) / prev
            if dev > self.price_deviation_pct:
                return self._reject(event, DataStatus.PRICE_SPIKE, f"mid moved {dev:.4f}")

        if last_ts is not None and self._now() - ts > self.staleness_seconds:
            return self._reject(event, DataStatus.STALE, f"quote age {self._now() - ts:.1f}s")

        self._seen_ids.add(event.event_id)
        self._last_ts[symbol] = ts
        self._last_mid[symbol] = mid
        self.report.record(DataStatus.VALID)
        return DataStatus.VALID

    def _reject(self, event: Event, status: DataStatus, reason: str) -> DataStatus:
        self.quarantine.append(QuarantinedItem(event.event_id, status, reason, event.payload))
        self.report.record(status)
        return status

    @staticmethod
    def _now() -> float:
        import time

        return time.time()

    def is_fresh(self, symbol: str) -> bool:
        ts = self._last_ts.get(symbol)
        return ts is not None and self._now() - ts <= self.staleness_seconds