
'''Market data validation and integrity engine (Module 03). Invalid or suspicious events are quarantined and never silently forwarded.'''

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Set, List
import time

from ..core.events import Event
from ..domain.models import Quote # Assuming Quote has symbol, timestamp, bid, ask, mid

class DataStatus(str, Enum):
    VALID = "VALID"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    PRICE_SPIKE = "PRICE_SPIKE"
    STALE = "STALE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INCONSISTENT = "INCONSISTENT"

@dataclass
class QuarantinedItem:
    event_id: str
    status: DataStatus
    reason: str
    payload: Any # Original event payload

@dataclass
class DataQualityReport:
    accepted: int = 0
    rejected: int = 0
    counts: Dict[DataStatus, int] = field(default_factory=lambda: {status: 0 for status in DataStatus})

    def record(self, status: DataStatus):
        if status == DataStatus.VALID:
            self.accepted += 1
        else:
            self.rejected += 1
        self.counts[status] = self.counts.get(status, 0) + 1

@dataclass
class MarketDataValidator:
    price_deviation_pct: float = 0.05
    staleness_seconds: float = 30.0
    max_backwards_skew: float = 5.0 # Max seconds an event can be older than last_ts for a symbol

    # State per symbol: (last_timestamp, last_mid_price)
    _last_market_data: Dict[str, Dict[str, float]] = field(default_factory=dict) # {symbol: {'ts': float, 'mid': float}}
    _seen_event_ids: Set[str] = field(default_factory=set)
    quarantine: List[QuarantinedItem] = field(default_factory=list)
    report: DataQualityReport = field(default_factory=DataQualityReport)

    def validate_quote(self, event: Event) -> bool:
        """
        Validates a market data quote event.
        Returns True if valid and processed, False if rejected and quarantined.
        """
        if not isinstance(event, Event) or not hasattr(event, 'payload'):
            # This case might be handled by upstream, but good to have a catch
            self._reject("UNKNOWN", DataStatus.INCONSISTENT, "Event object malformed or missing payload", event)
            return False

        event_id = getattr(event, 'id', str(id(event))) # Use event.id if available, otherwise generate
        payload = event.payload

        if isinstance(payload, Quote):
            quote = payload
        elif isinstance(payload, dict):
            # Attempt to construct Quote from dict, assuming keys match Quote fields
            try:
                quote = Quote(
                    symbol=payload['symbol'],
                    timestamp=payload['timestamp'],
                    bid=payload['bid'],
                    ask=payload['ask']
                )
                if 'mid' not in payload:
                    quote.mid = (quote.bid + quote.ask) / 2 # Calculate mid if not present
            except KeyError as e:
                self._reject(event_id, DataStatus.INCONSISTENT, f"Payload dict missing key: {e}", payload)
                return False
            except Exception as e:
                self._reject(event_id, DataStatus.INCONSISTENT, f"Failed to parse quote from dict: {e}", payload)
                return False
        else:
            self._reject(event_id, DataStatus.INCONSISTENT, "Payload is not a Quote instance or a dict", payload)
            return False

        symbol = quote.symbol
        timestamp = quote.timestamp
        mid_price = quote.mid

        # 1. Duplicate event ID
        if event_id in self._seen_event_ids:
            self._reject(event_id, DataStatus.DUPLICATE, "Event ID already processed", payload)
            return False
        self._seen_event_ids.add(event_id)

        current_time = time.time()

        # 2. Invalid Timestamp
        if not (0 < timestamp <= current_time + 60): # Allow up to 60s into future for clock skew
            self._reject(event_id, DataStatus.INVALID_TIMESTAMP, f"Timestamp {timestamp} is invalid (0 < ts <= now+60)", payload)
            return False

        # Get last known data for symbol
        last_data = self._last_market_data.get(symbol)
        if last_data:
            last_ts = last_data['ts']
            last_mid = last_data['mid']

            # 3. Out of order (older than last_ts minus skew)
            if timestamp < last_ts - self.max_backwards_skew:
                self._reject(event_id, DataStatus.OUT_OF_ORDER, f"Timestamp {timestamp} older than last processed {last_ts} by more than {self.max_backwards_skew}s", payload)
                return False

            # 4. Price spike detection (mid deviation) - only if timestamp is not significantly older
            if timestamp >= last_ts - 0.1: # Only check price deviation for fresh or slightly older quotes
                if abs(mid_price - last_mid) / last_mid > self.price_deviation_pct:
                    self._reject(event_id, DataStatus.PRICE_SPIKE, f"Price spike detected: {mid_price} vs last {last_mid}", payload)
                    return False

            # 5. Stale check (if quote is significantly older than wall clock and older than last_ts)
            # This check is more about the quote's timestamp vs. current time, if it's not the freshest for the symbol
            if timestamp < last_ts: # Only consider "stale" if it's truly older than what we've seen
                if current_time - timestamp > self.staleness_seconds:
                    self._reject(event_id, DataStatus.STALE, f"Quote timestamp {timestamp} is stale (older than {self.staleness_seconds}s from wall clock)", payload)
                    return False
        else:
            # For the very first quote for a symbol, if it's very old, consider it stale
            if current_time - timestamp > self.staleness_seconds:
                self._reject(event_id, DataStatus.STALE, f"First quote for {symbol} timestamp {timestamp} is stale (older than {self.staleness_seconds}s from wall clock)", payload)
                return False


        # If we reach here, the event is considered valid for processing
        self._last_market_data[symbol] = {'ts': timestamp, 'mid': mid_price}
        self.report.record(DataStatus.VALID)
        return True


    def _reject(self, event_id: str, status: DataStatus, reason: str, payload: Any):
        """Appends a QuarantinedItem and records the status."""
        item = QuarantinedItem(event_id=event_id, status=status, reason=reason, payload=payload)
        self.quarantine.append(item)
        self.report.record(status)

    def is_fresh(self, symbol: str) -> bool:
        """
        Checks if the last known market data for a symbol is within the staleness threshold.
        """
        last_data = self._last_market_data.get(symbol)
        if not last_data:
            return False # No data seen for this symbol
        last_ts = last_data['ts']
        return (time.time() - last_ts) <= self.staleness_seconds
