Typed event schemas.

Every event carries a unique identifier, an event timestamp, an ingestion
timestamp, a source identifier and a schema version. Correlation identifiers
are attached where an event is derived from another event.
'''

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def utc_now() -> float:
    """Current UTC time as a unix timestamp (seconds)."""
    return time.time()


def monotonic_now() -> float:
    """Monotonic clock reading for latency measurement."""
    return time.monotonic()


def new_id() -> str:
    return uuid.uuid4().hex


class EventType(str, Enum):
    QUOTE = "quote"
    TRADE = "trade"
    BAR = "bar"
    FEATURE = "feature"
    SIGNAL = "signal"
    TRADE_INTENT = "trade_intent"
    ORDER_INTENT = "order_intent"
    ORDER_UPDATE = "order_update"
    EXECUTION = "execution"
    SYSTEM = "system"
    AUDIT = "audit"


@dataclass(frozen=True)
class Event:
    """Base event envelope."""

    event_type: EventType
    payload: dict[str, Any]
    event_id: str = field(default_factory=new_id)
    source: str = "unknown"
    schema_version: str = "1.0.0"
    ts_event: float = 0.0
    ts_ingest: float = field(default_factory=utc_now)
    sequence: int | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if self.ts_event == 0.0:
            object.__setattr__(self, "ts_event", self.ts_ingest)


class EventBus:
    """In-process synchronous event bus.

    Local-development backbone. Subscribers are per-event-type callbacks.
    The bus is deterministic: subscribers are invoked in registration order,
    which makes backtests reproducible.
    """

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list] = {}
        self._sequence = 0

    def subscribe(self, event_type: EventType, handler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: Event) -> None:
        self._sequence += 1
        stamped = Event(
            event_type=event.event_type,
            payload=event.payload,
            event_id=event.event_id,
            source=event.source,
            schema_version=event.schema_version,
            ts_event=event.ts_event,
            ts_ingest=event.ts_ingest,
            sequence=self._sequence,
            correlation_id=event.correlation_id,
        )
        for handler in self._subscribers.get(stamped.event_type, []):
            handler(stamped)

    @property
    def sequence(self) -> int:
        return self._sequence