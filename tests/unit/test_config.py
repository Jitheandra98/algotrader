"""Config loader tests.

Cover the layering precedence (default < env-specific < local < env vars) and the
live-trading gate logic, which is safety-critical.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.config import Settings, load_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _isolate_env() -> Iterator[None]:
    saved = dict(os.environ)
    # Strip anything that could pollute the test settings.
    for k in list(os.environ):
        if k.startswith("ALGOTRADER_") or k == "LIVE":
            del os.environ[k]
    yield
    os.environ.clear()
    os.environ.update(saved)
    reset_settings_cache()


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_loads_default_when_only_default_present(tmp_config_dir: Path) -> None:
    _write(
        tmp_config_dir / "default.yaml",
        """
env: dev
logging:
  level: WARNING
  format: json
""",
    )
    reset_settings_cache()
    s = load_settings(env="dev", config_dir=tmp_config_dir)
    assert s.logging.level == "WARNING"
    assert s.logging.format == "json"


def test_env_yaml_overrides_default(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir / "default.yaml", "logging: {level: INFO, format: console}\n")
    _write(tmp_config_dir / "dev.yaml", "logging: {level: DEBUG}\n")
    reset_settings_cache()
    s = load_settings(env="dev", config_dir=tmp_config_dir)
    assert s.logging.level == "DEBUG"
    # `format` was not overridden — default survives.
    assert s.logging.format == "console"


def test_local_yaml_overrides_env(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir / "default.yaml", "logging: {level: INFO}\n")
    _write(tmp_config_dir / "dev.yaml", "logging: {level: DEBUG}\n")
    _write(tmp_config_dir / "local.yaml", "logging: {level: ERROR}\n")
    reset_settings_cache()
    s = load_settings(env="dev", config_dir=tmp_config_dir)
    assert s.logging.level == "ERROR"


def test_env_var_overrides_yaml(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir / "default.yaml", "logging: {level: INFO}\n")
    os.environ["ALGOTRADER_LOGGING__LEVEL"] = "DEBUG"
    reset_settings_cache()
    s = load_settings(env="dev", config_dir=tmp_config_dir)
    assert s.logging.level == "DEBUG"


def test_live_env_var_forces_paper_when_unset(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir / "default.yaml", "live: true\n")
    # LIVE explicitly false should win over YAML true.
    os.environ["LIVE"] = "false"
    reset_settings_cache()
    s = load_settings(env="dev", config_dir=tmp_config_dir)
    assert s.live is False


def test_live_env_var_can_enable(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir / "default.yaml", "live: false\n")
    os.environ["LIVE"] = "true"
    reset_settings_cache()
    s = load_settings(env="dev", config_dir=tmp_config_dir)
    assert s.live is True


# ---------- Live gating ----------


def _make_settings(*, live: bool, broker_live: bool, strategies: list[dict[str, object]]) -> Settings:
    return Settings(
        env="dev",
        live=live,
        broker={"name": "alpaca", "live": broker_live},
        strategies=strategies,
    )


def test_is_live_strategy_requires_all_three_gates() -> None:
    # Missing global
    s = _make_settings(
        live=False, broker_live=True, strategies=[{"name": "x", "enabled": True, "live": True}]
    )
    assert s.is_live_strategy("x") is False

    # Missing broker
    s = _make_settings(
        live=True, broker_live=False, strategies=[{"name": "x", "enabled": True, "live": True}]
    )
    assert s.is_live_strategy("x") is False

    # Missing per-strategy
    s = _make_settings(
        live=True, broker_live=True, strategies=[{"name": "x", "enabled": True, "live": False}]
    )
    assert s.is_live_strategy("x") is False

    # Strategy disabled even though live=true
    s = _make_settings(
        live=True, broker_live=True, strategies=[{"name": "x", "enabled": False, "live": True}]
    )
    assert s.is_live_strategy("x") is False

    # All three plus enabled — permitted.
    s = _make_settings(
        live=True, broker_live=True, strategies=[{"name": "x", "enabled": True, "live": True}]
    )
    assert s.is_live_strategy("x") is True


def test_is_live_strategy_unknown_strategy_returns_false() -> None:
    s = _make_settings(live=True, broker_live=True, strategies=[])
    assert s.is_live_strategy("does_not_exist") is False


def test_risk_caps_validated() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(risk={"max_per_trade_risk_pct": "0.10"})  # exceeds 0.05 cap
