"""Secrets provider tests."""

from __future__ import annotations

import os

import pytest

from core.secrets import EnvSecretProvider, SecretNotFound, VaultSecretProvider


def test_env_provider_reads_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    sp = EnvSecretProvider()
    assert sp.get("alpaca_api_key") == "test-key"


def test_env_provider_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    sp = EnvSecretProvider()
    with pytest.raises(SecretNotFound):
        sp.get("does_not_exist")


def test_env_provider_treats_empty_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPTY_SECRET", "")
    sp = EnvSecretProvider()
    with pytest.raises(SecretNotFound):
        sp.get("empty_secret")


def test_get_optional_returns_default() -> None:
    sp = EnvSecretProvider()
    assert sp.get_optional("definitely_not_set", default="fallback") == "fallback"


def test_secret_not_found_does_not_include_value() -> None:
    """The error message must reveal the missing key but never any value."""
    sp = EnvSecretProvider()
    try:
        sp.get("nonexistent")
    except SecretNotFound as exc:
        msg = str(exc)
        # Should mention the (upper-cased) key but nothing else.
        assert "NONEXISTENT" in msg


def test_vault_stub_raises() -> None:
    sp = VaultSecretProvider(addr="http://vault:8200", token="t")
    with pytest.raises(SecretNotFound, match="not implemented"):
        sp.get("alpaca_api_key")


def test_env_provider_strips_os_environ_independence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider always reads at access time, not at construction time."""
    sp = EnvSecretProvider()
    monkeypatch.setenv("DYNAMIC_KEY", "v1")
    assert sp.get("dynamic_key") == "v1"
    monkeypatch.setenv("DYNAMIC_KEY", "v2")
    assert sp.get("dynamic_key") == "v2"
    monkeypatch.delenv("DYNAMIC_KEY")
    assert os.environ.get("DYNAMIC_KEY") is None
    with pytest.raises(SecretNotFound):
        sp.get("dynamic_key")
