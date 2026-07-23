"""Logging redaction tests.

Goal: an accidental ``log.info("...", api_secret=k)`` does not leak the value.
"""

from __future__ import annotations

import io
import json
import logging
import sys

import structlog

from core.logging import configure_logging, get_logger


def _capture_json_logs() -> tuple[io.StringIO, logging.Handler]:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    return buf, h


def test_secret_keys_redacted_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Re-configure with JSON so we can parse output and inspect fields.
    configure_logging(level="DEBUG", fmt="json")

    captured: list[str] = []
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    # structlog's PrintLoggerFactory writes via sys.stdout; capture by replacing it.
    class _Capture:
        def write(self, s: str) -> None:
            captured.append(s)

        def flush(self) -> None:
            pass

    structlog.configure(
        processors=structlog.get_config()["processors"],
        wrapper_class=structlog.get_config()["wrapper_class"],
        logger_factory=structlog.PrintLoggerFactory(file=_Capture()),  # type: ignore[arg-type]
        cache_logger_on_first_use=False,
    )

    log = get_logger("test")
    log.info(
        "order_send",
        api_key="VERYSECRETKEY",
        api_secret="VERYSECRETSECRET",
        nested={"password": "p@ss"},
        ok_field="visible",
    )

    blob = "".join(captured)
    assert "VERYSECRETKEY" not in blob
    assert "VERYSECRETSECRET" not in blob
    assert "p@ss" not in blob
    assert "visible" in blob
    # Should be valid JSON
    payload = json.loads(blob.strip().splitlines()[-1])
    assert payload["api_key"] == "***REDACTED***"
    assert payload["nested"]["password"] == "***REDACTED***"


def test_get_logger_autoconfigures_when_called_first() -> None:
    # No prior configure_logging — should not raise.
    log = get_logger("autoconf")
    log.info("hello")
