"""Prometheus metrics tests."""

from __future__ import annotations

import pytest

from monitoring import MetricsRegistry


def test_metrics_register_and_increment() -> None:
    m = MetricsRegistry()
    m.on_signal_emitted("sma_crossover", "SPY", "LONG")
    m.on_signal_emitted("sma_crossover", "SPY", "LONG")
    m.on_signal_emitted("rsi_mean_reversion", "QQQ", "SHORT")
    out = m.export().decode()
    assert "algotrader_signals_emitted_total" in out
    assert 'strategy="sma_crossover"' in out


def test_metrics_gauges_track_latest_value() -> None:
    m = MetricsRegistry()
    m.set_portfolio(equity=100000, cash=80000, position_count=3, unrealized_pnl=500, realized_pnl_daily=-200)
    m.set_kill_switch(True)
    out = m.export().decode()
    assert "algotrader_equity_usd 100000" in out
    assert "algotrader_open_position_count 3" in out
    assert "algotrader_kill_switch_tripped 1" in out


def test_metrics_histograms_observe() -> None:
    m = MetricsRegistry()
    for ms in (10, 50, 100, 1500):
        m.observe_fill_latency(ms, broker="alpaca")
    out = m.export().decode()
    assert 'algotrader_fill_latency_ms_bucket{broker="alpaca",le="50.0"}' in out


def test_metrics_namespace_prefixes() -> None:
    m = MetricsRegistry(namespace="prod")
    m.on_signal_emitted("s", "SPY", "LONG")
    out = m.export().decode()
    assert "prod_algotrader_signals_emitted_total" in out


def test_metrics_health_status_per_check() -> None:
    m = MetricsRegistry()
    m.set_health("data_feed_gap", True)
    m.set_health("broker_connection", False)
    out = m.export().decode()
    assert 'algotrader_health_status{check="data_feed_gap"} 1' in out
    assert 'algotrader_health_status{check="broker_connection"} 0' in out
