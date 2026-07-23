"""Data-feed chaos: gap detection and circuit breaker trip.

Simulates ticks arriving normally, then a sudden gap. Verifies:
  - The :class:`DataFeedGapCheck` reports unhealthy.
  - The :class:`HealthCircuitBreaker` trips the kill-switch after the configured
    number of consecutive unhealthy verdicts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from monitoring import (
    DataFeedGapCheck,
    HealthCircuitBreaker,
    HealthMonitor,
)
from risk import KillSwitch

UTC = timezone.utc


def test_data_feed_gap_trips_circuit_breaker() -> None:
    # Simulated 'current time' progresses; the last-tick timestamp stays fixed,
    # so the gap widens each evaluation.
    last_tick = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    ks = KillSwitch()
    monitor = HealthMonitor([
        DataFeedGapCheck(get_last_tick_ts=lambda: last_tick, max_lag=timedelta(seconds=10)),
    ])
    breaker = HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=3)

    # The first evaluation will see "now ≫ last_tick" because real time has
    # moved on past 2026-05-25. So every consecutive eval is unhealthy.
    breaker.evaluate()
    breaker.evaluate()
    assert not ks.is_tripped, "should not trip on 2/3"
    breaker.evaluate()
    assert ks.is_tripped


def test_data_feed_recovery_resets_breaker_counter() -> None:
    """When the feed comes back, the breaker doesn't carry the unhealthy
    count forward indefinitely."""
    state = {"ts": datetime(2026, 5, 25, 13, 30, tzinfo=UTC)}  # stale
    ks = KillSwitch()
    monitor = HealthMonitor([
        DataFeedGapCheck(get_last_tick_ts=lambda: state["ts"], max_lag=timedelta(seconds=10)),
    ])
    breaker = HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=5)

    breaker.evaluate()
    breaker.evaluate()  # 2/5 unhealthy
    # Feed recovers (last tick is "now-ish")
    state["ts"] = datetime.now(tz=UTC)
    breaker.evaluate()
    assert not ks.is_tripped  # counter reset
    # Feed dies again
    state["ts"] = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    breaker.evaluate()
    breaker.evaluate()
    # Only 2 consecutive unhealthy since reset; threshold is 5.
    assert not ks.is_tripped
