"""Email alerts via SMTP.

Uses :mod:`smtplib` wrapped in ``asyncio.to_thread`` so the blocking SMTP call
doesn't stall the event loop. For high-volume traffic, swap to ``aiosmtplib``;
for operator alerts the throughput we need is fine with the threaded version.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from core.logging import get_logger
from monitoring.alerts.base import Alert, AlertChannel

log = get_logger(__name__)


class EmailChannel(AlertChannel):
    name = "email"

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        username: str,
        password: str,
        from_addr: str,
        to_addrs: list[str],
        use_tls: bool = True,
        timeout_s: float = 10.0,
    ) -> None:
        if not smtp_host or not to_addrs:
            raise ValueError("smtp_host and to_addrs are required")
        self._host = smtp_host
        self._port = smtp_port
        self._user = username
        self._pwd = password
        self._from = from_addr
        self._to = list(to_addrs)
        self._use_tls = use_tls
        self._timeout = timeout_s

    def build_message(self, alert: Alert) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = alert.label()
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        body = alert.body
        if alert.tags:
            body += "\n\nTags:\n" + "\n".join(f"  {k}: {v}" for k, v in alert.tags.items())
        msg.set_content(body)
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._user and self._pwd:
                smtp.login(self._user, self._pwd)
            smtp.send_message(msg)

    async def send(self, alert: Alert) -> None:
        msg = self.build_message(alert)
        await asyncio.to_thread(self._send_sync, msg)
        log.debug("email_alert_sent", title=alert.title, severity=alert.severity.value)
