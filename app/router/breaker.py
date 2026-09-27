"""Per-model circuit breaker: N consecutive transient failures -> open for T seconds,
then half-open allowing exactly one probe; success closes, failure re-opens."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from .clock import Clock

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class CircuitBreaker:
    def __init__(self, clock: Clock | None = None, failures: int = 3, open_seconds: float = 60.0):
        self.clock = clock or Clock()
        self.threshold = int(failures)
        self.open_seconds = float(open_seconds)
        self._s: dict[str, _State] = {}
        self._lock = threading.Lock()

    def _st(self, key: str) -> _State:
        return self._s.setdefault(key, _State())

    def state(self, key: str) -> str:
        s = self._st(key)
        if s.opened_at is None:
            return CLOSED
        if self.clock.now() - s.opened_at < self.open_seconds:
            return OPEN
        return HALF_OPEN

    def allow(self, key: str) -> tuple[bool, str]:
        """Non-mutating eligibility check used by the filter."""
        st = self.state(key)
        if st == OPEN:
            return False, "breaker:open"
        if st == HALF_OPEN and self._st(key).probe_in_flight:
            return False, "breaker:half_open_probe_in_flight"
        return True, st

    def acquire(self, key: str) -> bool:
        """Called right before an attempt; claims the half-open probe slot."""
        with self._lock:
            st = self.state(key)
            if st == OPEN:
                return False
            if st == HALF_OPEN:
                s = self._st(key)
                if s.probe_in_flight:
                    return False
                s.probe_in_flight = True
            return True

    def record_success(self, key: str) -> None:
        with self._lock:
            self._s[key] = _State()

    def record_failure(self, key: str, transient: bool = True) -> None:
        with self._lock:
            s = self._st(key)
            was_half_open = s.opened_at is not None and s.probe_in_flight
            s.probe_in_flight = False
            if not transient:
                return
            if was_half_open:
                s.opened_at = self.clock.now()
                s.failures = self.threshold
                return
            s.failures += 1
            if s.failures >= self.threshold:
                s.opened_at = self.clock.now()

    def release(self, key: str) -> None:
        """Release a half-open probe slot without an outcome (e.g. non-transient error)."""
        with self._lock:
            self._st(key).probe_in_flight = False
