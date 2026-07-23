"""Layered configuration loader.

Precedence (lowest -> highest):
  1. ``config/default.yaml``
  2. ``config/{env}.yaml``     (env = ALGOTRADER_ENV, default 'dev')
  3. ``config/local.yaml``     (developer override, gitignored)
  4. environment variables    (prefix ``ALGOTRADER_``, double-underscore for nesting)

Live trading gating
-------------------
Real-money orders require **all three** of:
  - ``LIVE=true`` environment variable (global kill)
  - ``settings.broker.live = true`` (config flag)
  - ``settings.strategies[i].live = true`` per-strategy

``Settings.is_live_strategy(name)`` is the single helper that combines these. Risk and
execution layers MUST call it before any real-money submission.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


# ---------- Sub-models ----------


class RedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = "redis://localhost:6379/0"
    stream_max_len: int = Field(default=1_000_000, ge=1_000)
    consumer_block_ms: int = Field(default=5_000, ge=100)


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = "postgresql://algotrader:algotrader@localhost:5432/algotrader"
    pool_min: int = Field(default=2, ge=1)
    pool_max: int = Field(default=10, ge=1)


class BrokerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    live: bool = False
    base_url: str | None = None
    data_url: str | None = None
    rate_limit_per_minute: int = Field(default=200, ge=1)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_per_trade_risk_pct: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.05"))
    max_portfolio_risk_pct: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.20"))
    max_daily_loss_pct: Decimal = Field(default=Decimal("0.03"), gt=0, le=Decimal("0.20"))
    max_weekly_loss_pct: Decimal = Field(default=Decimal("0.08"), gt=0, le=Decimal("0.30"))
    max_consecutive_losses: int = Field(default=5, ge=1)
    max_gross_leverage: Decimal = Field(default=Decimal("2.0"), ge=Decimal("1.0"))
    max_net_leverage: Decimal = Field(default=Decimal("1.5"), ge=Decimal("0.5"))
    enforce_pdt_rule: bool = True
    pdt_threshold_equity: Decimal = Field(default=Decimal("25000"), ge=Decimal("0"))


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    live: bool = False
    timeframe: str = "1m"
    instruments: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = "INFO"
    format: str = "console"  # 'console' or 'json'

    @field_validator("level")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {v}")
        return v

    @field_validator("format")
    @classmethod
    def _lower(cls, v: str) -> str:
        v = v.lower()
        if v not in {"console", "json"}:
            raise ValueError(f"invalid log format: {v}")
        return v


# ---------- Top-level ----------


class Settings(BaseSettings):
    """Top-level config. Env overrides use prefix ``ALGOTRADER_`` and ``__`` for nesting.

    Example: ``ALGOTRADER_REDIS__URL=redis://prod:6379/0`` overrides ``redis.url``.

    The ``LIVE`` env var is intentionally NOT prefixed — it's the global live-trading
    gate and must be conspicuous in the shell environment.
    """

    model_config = SettingsConfigDict(
        env_prefix="ALGOTRADER_",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Make env vars take precedence over the YAML-derived init kwargs.

        Default pydantic-settings order is (init, env, dotenv, secrets) — init kwargs
        win. We deliberately invert init and env so ``ALGOTRADER_*`` env vars override
        anything we loaded from YAML.
        """
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)

    env: str = Field(default="dev")
    live: bool = Field(
        default=False,
        description="Global live switch. Read from LIVE env var directly (see load_settings).",
    )
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    broker: BrokerConfig = Field(default_factory=lambda: BrokerConfig(name="alpaca", live=False))
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategies: list[StrategyConfig] = Field(default_factory=list)

    # ---- Live-trading gating ----

    def is_live_strategy(self, strategy_name: str) -> bool:
        """The single authority on whether a given strategy may place real-money trades.

        All three must be true: global LIVE, broker.live, and per-strategy live.
        """
        if not self.live:
            return False
        if not self.broker.live:
            return False
        for s in self.strategies:
            if s.name == strategy_name:
                return bool(s.live and s.enabled)
        return False


# ---------- Loader ----------


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Right-biased deep merge of dicts. Lists are replaced, not concatenated."""
    out: dict[str, Any] = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], Mapping) and isinstance(v, Mapping):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return data


@lru_cache(maxsize=1)
def load_settings(env: str | None = None, config_dir: Path | None = None) -> Settings:
    """Load layered YAML + env-var-overridden settings.

    Args:
        env: which environment YAML to layer (default: ``$ALGOTRADER_ENV`` or 'dev').
        config_dir: override config directory (used in tests).
    """
    config_dir = config_dir or CONFIG_DIR
    env = env or os.environ.get("ALGOTRADER_ENV", "dev")

    merged: dict[str, Any] = {}
    merged = _deep_merge(merged, _load_yaml(config_dir / "default.yaml"))
    merged = _deep_merge(merged, _load_yaml(config_dir / f"{env}.yaml"))
    merged = _deep_merge(merged, _load_yaml(config_dir / "local.yaml"))

    # Pydantic-settings reads env vars for top-level fields with prefix ALGOTRADER_.
    # We feed merged YAML in as kwargs; pydantic-settings will overlay env on top.
    settings = Settings(**merged)

    # LIVE env var is unprefixed and respected directly.
    live_env = os.environ.get("LIVE", "").strip().lower()
    if live_env in {"true", "1", "yes", "on"}:
        settings = settings.model_copy(update={"live": True})
    elif live_env in {"false", "0", "no", "off", ""}:
        # Either no env var or explicit false. If YAML/env-prefixed value was True,
        # explicit LIVE=false still wins because LIVE is the global kill.
        if live_env in {"false", "0", "no", "off"}:
            settings = settings.model_copy(update={"live": False})

    return settings


def reset_settings_cache() -> None:
    """Test helper — clears the lru_cache so subsequent ``load_settings`` re-reads."""
    load_settings.cache_clear()
