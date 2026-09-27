"""Injectable clock so quota windows, breaker timers and retries are testable."""
from __future__ import annotations

import time


class Clock:
    def now(self) -> float:
        return time.time()

    def monotonic_ms(self) -> float:
        return time.monotonic() * 1000.0

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Deterministic clock for tests; `sleep` advances time instantly."""

    def __init__(self, start: float = 1_790_000_000.0):
        self.t = float(start)

    def now(self) -> float:
        return self.t

    def monotonic_ms(self) -> float:
        return self.t * 1000.0

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.t += seconds
