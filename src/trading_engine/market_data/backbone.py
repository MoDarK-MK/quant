import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, List, Optional

from ..core.events import Event, EventBus, EventType
from ..data_validation.validator import DataStatus
from ..domain.models import Quote


@dataclass(frozen=True)
class ReplayRow:
    symbol: str
    bid: Decimal
    ask: Decimal
    ts: float
    source: str
    sequence: int


def load_quote_csv(path: Path) -> List[ReplayRow]:
    rows = []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                ReplayRow(
                    symbol=row['symbol'],
                    bid=Decimal(row['bid']),
                    ask=Decimal(row['ask']),
                    ts=float(row['ts']),
                    source=row['source'],
                    sequence=int(row['sequence']),
                )
            )
    rows.sort(key=lambda r: (r.ts, r.sequence))
    return rows


class MarketDataBackbone:
    def __init__(self, bus: EventBus, source: str):
        self.bus = bus
        self.source = source

    def publish_quote(self, quote: Quote, correlation_id: Optional[str] = None):
        event = Event(
            EventType.QUOTE,
            payload={"quote": quote, "symbol": quote.symbol},
            correlation_id=correlation_id,
        )
        self.bus.publish(event)


class Replayer:
    def __init__(self, backbone: MarketDataBackbone):
        self.backbone = backbone

    def replay(
        self, rows: List[ReplayRow], validator: Optional[Callable] = None
    ) -> int:
        emitted_count = 0
        for row in rows:
            quote = Quote(
                symbol=row.symbol,
                bid=row.bid,
                ask=row.ask,
                ts=row.ts,
                source=row.source,
                sequence=row.sequence,
            )
            if validator:
                probe_event = Event(
                    EventType.QUOTE, payload={"quote": quote, "symbol": quote.symbol}
                )
                validation_status = validator.validate_quote(probe_event)
                if validation_status != DataStatus.OK:
                    continue  # Skip invalid quotes

            self.backbone.publish_quote(quote)
            emitted_count += 1
        return emitted_count