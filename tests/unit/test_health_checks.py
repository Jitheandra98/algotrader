"""Health check + monitor tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from monitoring import (
    BrokerConnectionCheck,
    DataFeedGapCheck,
    DrawdownCheck,
    HealthMonitor,
    LatencyCheck,
    OrderRejectionRateCheck,
)

UTC = timezone.utc


def test_data_feed_gap_healthy_within_lag() -> None:
    now = datetime.now(tz=UTC)
    chk = DataFeedGapCheck(get_last_tick_ts=lambda: now, max_lag=timedelta(seconds=5))
    s = chk.check()
    assert s.healthy
    assert "lag=" in s.detail


def test_data_feed_gap_unhealthy_when_stale() -> None:
    stale = datetime.now(tz=UTC) - timedelta(minutes=2)
    chk = DataFeedGapCheck(get_last_tick_ts=lambda: stale, max_lag=timedelta(seconds=5))
    s = chk.check()
    assert not s.healthy


def test_data_feed_gap_handles_no_data() -> None:
    chk = DataFeedGapCheck(get_last_tick_ts=lambda: None, max_lag=timedelta(seconds=5))
    s = chk.check()
    assert not s.healthy
    assert "no ticks" in s.detail


def test_broker_connection_uses_probe() -> None:
    s1 = BrokerConnectionCheck(is_connected=lambda: True).check()
    s2 = BrokerConnectionCheck(is_connected=lambda: False).check()
    assert s1.healthy and not s2.healthy


def test_latency_check_under_cap() -> None:
    s = LatencyCheck(get_p95_ms=lambda: 100.0, max_p95_ms=200).check()
    assert s.healthy
    assert s.metadata["p95_ms"] == 100.0


def test_latency_check_over_cap() -> None:
    s = LatencyCheck(get_p95_ms=lambda: 300.0, max_p95_ms=200).check()
    assert not s.healthy


def test_order_rejection_rate_below_min_sample_is_ok() -> None:
    chk = OrderRejectionRateCheck(
        get_submitted=lambda: 5, get_rejected=lambda: 3, max_rate=0.1, min_sample=20,
    )
    s = chk.check()
    assert s.healthy


def test_order_rejection_rate_over_cap() -> None:
    chk = OrderRejectionRateCheck(
        get_submitted=lambda: 100, get_rejected=lambda: 25, max_rate=0.10, min_sample=10,
    )
    s = chk.check()
    assert not s.healthy
    assert s.metadata["rate"] == 0.25


def test_drawdown_check_within_warning() -> None:
    s = DrawdownCheck(get_drawdown_pct=lambda: -2.0, warn_pct=5.0).check()
    assert s.healthy


def test_drawdown_check_breaches() -> None:
    s = DrawdownCheck(get_drawdown_pct=lambda: -7.0, warn_pct=5.0).check()
    assert not s.healthy


def test_health_monitor_aggregates_all() -> None:
    mon = HealthMonitor([
        BrokerConnectionCheck(is_connected=lambda: True),
        DrawdownCheck(get_drawdown_pct=lambda: -1.0, warn_pct=5.0),
    ])
    statuses = mon.run_all()
    assert set(statuses.keys()) == {"broker_connection", "drawdown"}
    assert mon.overall_healthy()


def test_health_monitor_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        HealthMonitor([
            BrokerConnectionCheck(is_connected=lambda: True),
            BrokerConnectionCheck(is_connected=lambda: False),
        ])


def test_health_monitor_traps_check_exceptions() -> None:
    class _Boom(BrokerConnectionCheck):
        name = "boom"
        def check(self):
            raise RuntimeError("explode")

    mon = HealthMonitor([_Boom(is_connected=lambda: True)])
    s = mon.run_all()
    assert not s["boom"].healthy
    assert "explode" in s["boom"].detail
