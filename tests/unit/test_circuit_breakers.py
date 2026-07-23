"""Circuit breaker tests — verify the kill switch trips correctly."""

from __future__ import annotations

import pytest

from monitoring import (
    BrokerConnectionCheck,
    CircuitBreakerSet,
    DrawdownCircuitBreaker,
    HealthCircuitBreaker,
    HealthMonitor,
    LatencyCircuitBreaker,
)
from risk import KillSwitch


def test_health_breaker_trips_after_consecutive_unhealthy() -> None:
    ks = KillSwitch()
    monitor = HealthMonitor([BrokerConnectionCheck(is_connected=lambda: False)])
    breaker = HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=3)

    breaker.evaluate()
    breaker.evaluate()
    assert not ks.is_tripped, "should not trip on 2/3"
    breaker.evaluate()
    assert ks.is_tripped


def test_health_breaker_resets_count_on_healthy_verdict() -> None:
    ks = KillSwitch()
    state = {"ok": False}
    monitor = HealthMonitor([BrokerConnectionCheck(is_connected=lambda: state["ok"])])
    breaker = HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=3)

    breaker.evaluate()
    breaker.evaluate()
    state["ok"] = True
    breaker.evaluate()  # healthy → resets counter
    state["ok"] = False
    breaker.evaluate()
    breaker.evaluate()
    # Counter reset after healthy; needs 3 more to trip.
    assert not ks.is_tripped


def test_drawdown_breaker_trips_on_first_breach() -> None:
    ks = KillSwitch()
    dd = {"v": -2.0}
    breaker = DrawdownCircuitBreaker(kill_switch=ks, max_pct=5.0, get_drawdown_pct=lambda: dd["v"])

    breaker.evaluate()
    assert not ks.is_tripped
    dd["v"] = -5.5
    breaker.evaluate()
    assert ks.is_tripped


def test_latency_breaker_trips_after_sustained_breach() -> None:
    ks = KillSwitch()
    p95 = {"v": 100.0}
    breaker = LatencyCircuitBreaker(kill_switch=ks, get_p95_ms=lambda: p95["v"], max_p95_ms=200, trip_after=2)
    breaker.evaluate()
    p95["v"] = 250.0
    breaker.evaluate()
    assert not ks.is_tripped
    breaker.evaluate()
    assert ks.is_tripped


def test_breaker_set_runs_all() -> None:
    ks = KillSwitch()
    breakers = CircuitBreakerSet([
        DrawdownCircuitBreaker(kill_switch=ks, max_pct=5.0, get_drawdown_pct=lambda: -1.0),
    ])
    breakers.evaluate_all()
    assert not ks.is_tripped


def test_breaker_does_not_re_trip_when_already_active() -> None:
    """Once the kill switch is on, the breaker doesn't keep adding trip events."""
    ks = KillSwitch()
    ks.trip("preexisting")
    breaker = DrawdownCircuitBreaker(kill_switch=ks, max_pct=5.0, get_drawdown_pct=lambda: -7.0)
    breaker.evaluate()
    assert breaker.trip_events == []
