Latency metrics and audit trail (Module 10).'''

'''
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean


@dataclass
class LatencyStats:
    samples_ms: list[float] = field(default_factory=list)

    def record(self, ms: float) -> None:
        self.samples_ms.append(ms)

    def percentile(self, p: float) -> float:
        if not self.samples_ms:
            return 0.0
        s = sorted(self.samples_ms)
        idx = min(len(s) - 1, int(p / 100 * len(s)))
        return s[idx]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def mean(self) -> float:
        return fmean(self.samples_ms) if self.samples_ms else 0.0


class AuditTrail:
    """Append-only record of decisions for post-trade review."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, actor: str, action: str, detail: dict) -> None:
        self.entries.append({"actor": actor, "action": action, **detail})

    def __len__(self) -> int:
        return len(self.entries)
