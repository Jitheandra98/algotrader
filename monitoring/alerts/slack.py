"""Slack alerts via incoming webhook URL."""

from __future__ import annotations

from typing import Any

import httpx

from core.logging import get_logger
from monitoring.alerts.base import Alert, AlertChannel, AlertSeverity

log = get_logger(__name__)


_SLACK_COLOR = {
    AlertSeverity.INFO: "#2196F3",
    AlertSeverity.WARNING: "#FFC107",
    AlertSeverity.ERROR: "#FF5722",
    AlertSeverity.CRITICAL: "#B71C1C",
}


class SlackChannel(AlertChannel):
    name = "slack"

    def __init__(self, webhook_url: str, *, timeout_s: float = 5.0) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self._url = webhook_url
        self._timeout = timeout_s

    def build_payload(self, alert: Alert) -> dict[str, Any]:
        fields = [
            {"title": k, "value": v, "short": True} for k, v in alert.tags.items()
        ]
        return {
            "attachments": [
                {
                    "color": _SLACK_COLOR.get(alert.severity, "#9E9E9E"),
                    "title": alert.label(),
                    "text": alert.body,
                    "fields": fields,
                    "ts": int(alert.timestamp.timestamp()),
                }
            ]
        }

    async def send(self, alert: Alert) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=self.build_payload(alert))
            resp.raise_for_status()
        log.debug("slack_alert_sent", title=alert.title, severity=alert.severity.value)
