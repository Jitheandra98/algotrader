"""Twilio SMS alerts.

Uses Twilio's REST API directly (no SDK dependency). Authenticates via account
SID + auth token; sends one SMS per recipient.

In paper/dev environments, skip the channel entirely by not registering it —
sending SMS for INFO-level alerts is noisy and costs $$. Reserve SMS for
CRITICAL only.
"""

from __future__ import annotations

import httpx

from core.logging import get_logger
from monitoring.alerts.base import Alert, AlertChannel

log = get_logger(__name__)


class TwilioSMSChannel(AlertChannel):
    name = "twilio_sms"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_numbers: list[str],
        timeout_s: float = 10.0,
    ) -> None:
        if not (account_sid and auth_token and from_number and to_numbers):
            raise ValueError("account_sid, auth_token, from_number, to_numbers all required")
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._to = list(to_numbers)
        self._timeout = timeout_s
        self._url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    def build_body(self, alert: Alert) -> str:
        # SMS is character-limited; truncate aggressively.
        body = f"{alert.label()}\n{alert.body}"
        return body[:300] + ("…" if len(body) > 300 else "")

    async def send(self, alert: Alert) -> None:
        body = self.build_body(alert)
        async with httpx.AsyncClient(timeout=self._timeout, auth=(self._sid, self._token)) as client:
            for number in self._to:
                resp = await client.post(
                    self._url,
                    data={"From": self._from, "To": number, "Body": body},
                )
                resp.raise_for_status()
        log.debug("twilio_sms_sent", title=alert.title, severity=alert.severity.value, n_recipients=len(self._to))
