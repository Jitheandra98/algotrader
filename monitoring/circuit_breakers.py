"""Automatic circuit breakers.

A circuit breaker watches a condition and, when tripped, calls
:meth:`risk.KillSwitch.trip` to halt new entries. Per the brief:

  "Wire automatic circuit breakers that halt new entries on degraded health or
   drawdown breach."

The pattern: the breaker is *armed* — it watches a stream of signals (health
verdicts, drawdown readings, latency samples). When the trip condition holds
for ``trip_after`` consecutive samples (or one severe one), the kill-switch
trips and an alert fires.

Manual reset is required — the operator confirms the condition is resolved
before the bot can take new entries again.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from core.logging import get_logger
from monitoring.health import HealthMonitor, HealthStatus
from risk import KillSwitch

log = get_logger(__name__)

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class TripEvent:
    breaker: str
    timestamp: datetime
    reason: str


class CircuitBreaker(ABC):
    """Watches a condition; trips the linked :class:`KillSwitch` when it fires."""

    name: str

    def __init__(self, *, kill_switch: KillSwitch) -> None:
        self._ks = kill_switch
        self._trip_events: list[TripEvent] = []

    @property
    def trip_events(self) -> list[TripEvent]:
        return list(self._trip_events)

    def _trip(self, reason: str) -> None:
        now = datetime.now(tz=UTC)
        evt = TripEvent(breaker=self.name, timestamp=now, reason=reason)
        self._trip_events.append(evt)
        self._ks.trip(f"[{self.name}] {reason}")
        log.critical("circuit_breaker_tripped", breaker=self.name, reason=reason)

    @abstractmethod
    def evaluate(self) -> None:
        """Called by the watchdog loop. Implementations decide whether to trip."""


class HealthCircuitBreaker(CircuitBreaker):
    """Trips after ``trip_after`` consecutive unhealthy verdicts from the monitor."""

    name = "health"

    def __init__(
        self,
        *,
        kill_switch: KillSwitch,
        monitor: HealthMonitor,
        trip_after: int = 3,
    ) -> None:
        super().__init__(kill_switch=kill_switch)
        if trip_after < 1:
            raise ValueError("trip_after must be >= 1")
        self._monitor = monitor
        self._trip_after = trip_after
        self._consecutive = 0

    def evaluate(self) -> None:
        if self._ks.is_tripped:
            return
        statuses = self._monitor.run_all()
        unhealthy = [s for s in statuses.values() if not s.healthy]
        if unhealthy:
            self._consecutive += 1
            if self._consecutive >= self._trip_after:
                names = ", ".join(s.name for s in unhealthy)
                self._trip(f"{self._consecutive} consecutive unhealthy: {names}")
                self._consecutive = 0
        else:
            self._consecutive = 0


class DrawdownCircuitBreaker(CircuitBreaker):
    """Hard auto-trip when drawdown crosses ``max_pct``. One-shot — DD doesn't
    have a 'consecutive' notion."""

    name = "drawdown"

    def __init__(
        self,
        *,
        kill_switch: KillSwitch,
        max_pct: float,
        get_drawdown_pct,
    ) -> None:
        super().__init__(kill_switch=kill_switch)
        if max_pct <= 0:
            raise ValueError("max_pct must be > 0")
        self._max = max_pct
        self._get = get_drawdown_pct

    def evaluate(self) -> None:
        if self._ks.is_tripped:
            return
        dd = self._get()  # negative %
        if abs(dd) >= self._max:
            self._trip(f"drawdown {dd:.2f}% breached cap -{self._max:.2f}%")


class LatencyCircuitBreaker(CircuitBreaker):
    """Trips when fill-latency p95 stays above ``max_p95_ms`` for ``trip_after``
    consecutive evaluations."""

    name = "latency"

    def __init__(
        self,
        *,
        kill_switch: KillSwitch,
        get_p95_ms,
        max_p95_ms: float,
        trip_after: int = 3,
    ) -> None:
        super().__init__(kill_switch=kill_switch)
        if max_p95_ms <= 0:
            raise ValueError("max_p95_ms must be > 0")
        if trip_after < 1:
            raise ValueError("trip_after must be >= 1")
        self._cap = max_p95_ms
        self._get = get_p95_ms
        self._trip_after = trip_after
        self._consecutive = 0

    def evaluate(self) -> None:
        if self._ks.is_tripped:
            return
        p95 = self._get()
        if p95 is None:
            return
        if p95 > self._cap:
            self._consecutive += 1
            if self._consecutive >= self._trip_after:
                self._trip(f"fill latency p95 {p95:.0f}ms > cap {self._cap:.0f}ms")
                self._consecutive = 0
        else:
            self._consecutive = 0


class CircuitBreakerSet:
    """Convenience runner — calls every breaker's evaluate() in order."""

    def __init__(self, breakers: list[CircuitBreaker]) -> None:
        self._breakers = list(breakers)

    @property
    def breakers(self) -> list[CircuitBreaker]:
        return list(self._breakers)

    def evaluate_all(self) -> None:
        for b in self._breakers:
            try:
                b.evaluate()
            except Exception:
                log.exception("circuit_breaker_evaluate_error", breaker=b.name)
