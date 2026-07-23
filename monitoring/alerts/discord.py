"""Discord alerts via webhook."""

from __future__ import annotations

from typing import Any

import httpx

from core.logging import get_logger
from monitoring.alerts.base import Alert, AlertChannel, AlertSeverity

log = get_logger(__name__)


_DISCORD_COLOR = {
    AlertSeverity.INFO: 0x2196F3,
    AlertSeverity.WARNING: 0xFFC107,
    AlertSeverity.ERROR: 0xFF5722,
    AlertSeverity.CRITICAL: 0xB71C1C,
}


class DiscordChannel(AlertChannel):
    name = "discord"

    def __init__(self, webhook_url: str, *, timeout_s: float = 5.0) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self._url = webhook_url
        self._timeout = timeout_s

    def build_payload(self, alert: Alert) -> dict[str, Any]:
        fields = [
            {"name": k, "value": str(v), "inline": True} for k, v in alert.tags.items()
        ]
        return {
            "embeds": [
                {
                    "title": alert.label(),
                    "description": alert.body,
                    "color": _DISCORD_COLOR.get(alert.severity, 0x9E9E9E),
                    "fields": fields,
                    "timestamp": alert.timestamp.isoformat(),
                }
            ]
        }

    async def send(self, alert: Alert) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=self.build_payload(alert))
            resp.raise_for_status()
        log.debug("discord_alert_sent", title=alert.title, severity=alert.severity.value)
