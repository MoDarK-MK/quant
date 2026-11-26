Market data backbone (Module 01) with deterministic CSV replay.

The replay adapter preserves source provenance, sequence numbers and original
timestamps so backtests and replays are reproducible.
'''

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..core.events import Event, EventBus, EventType
from ..data_validation.validator import DataStatus
from ..domain.models import Quote


@dataclass
class ReplayRow:
    symbol: str
    bid: Decimal
    ask: Decimal
    ts: float
    source: str
    sequence: int


def load_quote_csv(path: str | Path) -> list[ReplayRow]:
    """Load a quotes CSV with columns: symbol,bid,ask,ts,source,sequence."""
    rows: list[ReplayRow] = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                ReplayRow(
                    symbol=row["symbol"],
                    bid=Decimal(row["bid"]),
                    ask=Decimal(row["ask"]),
                    ts=float(row["ts"]),
                    source=row.get("source", "csv"),
                    sequence=int(row["sequence"]),
                )
            )
    rows.sort(key=lambda r: (r.ts, r.sequence))
    return rows


class MarketDataBackbone:
    """Publishes validated market events on the bus."""

    def __init__(self, bus: EventBus, source: str = "backbone") -> None:
        self.bus = bus
        self.source = source

    def publish_quote(self, quote: Quote, correlation_id: str | None = None) -> Event:
        event = Event(
            event_type=EventType.QUOTE,
            payload={"quote": quote, "symbol": quote.symbol},
            source=self.source,
            ts_event=quote.ts,
            sequence=quote.sequence,
            correlation_id=correlation_id,
        )
        self.bus.publish(event)
        return event


class Replayer:
    """Deterministic historical replay: emits quotes as fast as consumers allow."""

    def __init__(self, backbone: MarketDataBackbone) -> None:
        self.backbone = backbone

    def replay(self, rows: list[ReplayRow], validator=None) -> int:
        emitted = 0
        for row in rows:
            quote = Quote(row.symbol, row.bid, row.ask, row.ts, row.source, row.sequence)
            if validator is not None:
                probe = Event(event_type=EventType.QUOTE, payload={"quote": quote},
                              source=row.source, ts_event=row.ts, sequence=row.sequence)
                if validator.validate_quote(probe) != DataStatus.VALID:
                    continue
            self.backbone.publish_quote(quote)
            emitted += 1
        return emitted