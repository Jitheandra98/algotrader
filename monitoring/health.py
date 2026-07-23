"""Health checks.

Each check returns a :class:`HealthStatus`. The :class:`HealthMonitor` runs the
suite on demand or on a timer and emits metrics. Failed checks feed the
circuit-breaker layer (:mod:`monitoring.circuit_breakers`), which trips the
risk kill-switch when health degrades.

Built-in checks:
  - :class:`DataFeedGapCheck` — last tick/bar arrived within ``max_lag``.
  - :class:`BrokerConnectionCheck` — the broker's HTTP/WS sessions are live.
  - :class:`LatencyCheck` — recent fill-latency p95 below threshold.
  - :class:`OrderRejectionRateCheck` — rejections / submissions over a window.
  - :class:`DrawdownCheck` — current DD below threshold (informational; the
    drawdown *limit* (M6) is what actually blocks trades).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core.logging import get_logger

log = get_logger(__name__)

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class HealthStatus:
    name: str
    healthy: bool
    detail: str
    last_checked: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthCheck(ABC):
    """One health check. Implementations return a status snapshot on demand."""

    name: str

    @abstractmethod
    def check(self) -> HealthStatus: ...


class DataFeedGapCheck(HealthCheck):
    """Unhealthy when the latest tick/bar is older than ``max_lag`` from now."""

    name = "data_feed_gap"

    def __init__(self, *, get_last_tick_ts: Callable[[], datetime | None], max_lag: timedelta) -> None:
        self._get_last_ts = get_last_tick_ts
        self._max_lag = max_lag

    def check(self) -> HealthStatus:
        now = datetime.now(tz=UTC)
        last = self._get_last_ts()
        if last is None:
            return HealthStatus(
                name=self.name, healthy=False, detail="no ticks yet", last_checked=now,
                metadata={"max_lag_s": self._max_lag.total_seconds()},
            )
        lag = now - last
        healthy = lag <= self._max_lag
        return HealthStatus(
            name=self.name,
            healthy=healthy,
            detail=f"lag={lag.total_seconds():.1f}s (cap {self._max_lag.total_seconds():.1f}s)",
            last_checked=now,
            metadata={"lag_s": lag.total_seconds()},
        )


class BrokerConnectionCheck(HealthCheck):
    """Unhealthy when the broker's connection probe returns False or raises."""

    name = "broker_connection"

    def __init__(self, *, is_connected: Callable[[], bool]) -> None:
        self._is_connected = is_connected

    def check(self) -> HealthStatus:
        now = datetime.now(tz=UTC)
        try:
            ok = bool(self._is_connected())
        except Exception as exc:
            return HealthStatus(
                name=self.name, healthy=False, detail=f"probe error: {exc}", last_checked=now,
            )
        return HealthStatus(
            name=self.name,
            healthy=ok,
            detail="connected" if ok else "disconnected",
            last_checked=now,
        )


class LatencyCheck(HealthCheck):
    """Unhealthy when recent fill latency p95 > ``max_p95_ms``."""

    name = "fill_latency"

    def __init__(self, *, get_p95_ms: Callable[[], float | None], max_p95_ms: float) -> None:
        self._get = get_p95_ms
        self._cap = max_p95_ms

    def check(self) -> HealthStatus:
        now = datetime.now(tz=UTC)
        p95 = self._get()
        if p95 is None:
            return HealthStatus(name=self.name, healthy=True, detail="no fills yet", last_checked=now)
        healthy = p95 <= self._cap
        return HealthStatus(
            name=self.name,
            healthy=healthy,
            detail=f"p95={p95:.0f}ms (cap {self._cap:.0f}ms)",
            last_checked=now,
            metadata={"p95_ms": p95},
        )


class OrderRejectionRateCheck(HealthCheck):
    """Unhealthy when rejections/submissions over a window exceeds ``max_rate``."""

    name = "order_rejection_rate"

    def __init__(
        self,
        *,
        get_submitted: Callable[[], int],
        get_rejected: Callable[[], int],
        max_rate: float = 0.10,
        min_sample: int = 20,
    ) -> None:
        if not 0 < max_rate <= 1:
            raise ValueError("max_rate must be in (0, 1]")
        if min_sample < 1:
            raise ValueError("min_sample must be >= 1")
        self._get_submitted = get_submitted
        self._get_rejected = get_rejected
        self._max_rate = max_rate
        self._min_sample = min_sample

    def check(self) -> HealthStatus:
        now = datetime.now(tz=UTC)
        sub = self._get_submitted()
        rej = self._get_rejected()
        if sub < self._min_sample:
            return HealthStatus(
                name=self.name,
                healthy=True,
                detail=f"sample {sub} < {self._min_sample}",
                last_checked=now,
                metadata={"submitted": sub, "rejected": rej},
            )
        rate = rej / sub if sub else 0.0
        healthy = rate <= self._max_rate
        return HealthStatus(
            name=self.name,
            healthy=healthy,
            detail=f"reject rate {rate * 100:.1f}% (cap {self._max_rate * 100:.1f}%)",
            last_checked=now,
            metadata={"rate": rate, "submitted": sub, "rejected": rej},
        )


class DrawdownCheck(HealthCheck):
    """Reports current drawdown vs caps. Doesn't block trades — the M6
    drawdown *limit* does. This is the health-monitor signal."""

    name = "drawdown"

    def __init__(self, *, get_drawdown_pct: Callable[[], float], warn_pct: float = 5.0) -> None:
        self._get = get_drawdown_pct
        self._warn = warn_pct
        if self._warn <= 0:
            raise ValueError("warn_pct must be > 0")

    def check(self) -> HealthStatus:
        now = datetime.now(tz=UTC)
        dd = self._get()  # negative percentage; e.g. -3.5 means -3.5%
        healthy = abs(dd) <= self._warn
        return HealthStatus(
            name=self.name,
            healthy=healthy,
            detail=f"dd={dd:.2f}% (warn at -{self._warn:.1f}%)",
            last_checked=now,
            metadata={"dd_pct": dd, "warn_pct": -self._warn},
        )


class HealthMonitor:
    """Runs all registered checks on demand. Caches the last results."""

    def __init__(self, checks: list[HealthCheck]) -> None:
        names_seen: set[str] = set()
        for c in checks:
            if c.name in names_seen:
                raise ValueError(f"duplicate health check name: {c.name}")
            names_seen.add(c.name)
        self._checks = list(checks)
        self._last: dict[str, HealthStatus] = {}

    @property
    def checks(self) -> list[HealthCheck]:
        return list(self._checks)

    def run_all(self) -> dict[str, HealthStatus]:
        for c in self._checks:
            try:
                self._last[c.name] = c.check()
            except Exception as exc:
                self._last[c.name] = HealthStatus(
                    name=c.name,
                    healthy=False,
                    detail=f"check raised: {exc}",
                    last_checked=datetime.now(tz=UTC),
                )
                log.exception("health_check_error", check=c.name)
        return dict(self._last)

    def last(self) -> dict[str, HealthStatus]:
        return dict(self._last)

    def overall_healthy(self) -> bool:
        if not self._last:
            return True  # nothing checked yet; don't trip
        return all(s.healthy for s in self._last.values())
