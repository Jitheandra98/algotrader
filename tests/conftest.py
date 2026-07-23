"""Shared pytest fixtures.

Kept intentionally minimal so unit tests can run with zero docker. Integration tests
get their own fixtures in ``tests/integration/conftest.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import reset_settings_cache
from core.schemas import AssetClass, Instrument


@pytest.fixture(autouse=True)
def _clean_settings_cache() -> None:
    """Ensure each test gets a fresh settings load (env var changes in tests apply)."""
    reset_settings_cache()


@pytest.fixture
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


@pytest.fixture
def btc_usd() -> Instrument:
    return Instrument(symbol="BTC/USD", asset_class=AssetClass.CRYPTO, venue="CBSE")


@pytest.fixture
def aapl_call() -> Instrument:
    return Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        venue="OPRA",
        strike=Decimal("200"),
        expiry=datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc),
        option_right="C",
    )


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Empty config dir for layered-config tests to populate per-test."""
    return tmp_path
