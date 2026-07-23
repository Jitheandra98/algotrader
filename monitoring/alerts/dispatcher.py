"""Rule-based multi-channel alert dispatcher.

Routes :class:`Alert` instances to one or more :class:`AlertChannel` instances
based on configured :class:`AlertRule` predicates. Each rule independently
decides whether an alert is in-scope; matching channels fan out in parallel.

Failures in one channel never block others (and never raise to the caller).
The dispatcher logs delivery errors so the operator can investigate.

Per the brief: 'multi-factor and dynamic alerts routed to Slack, Discord,
email, and SMS via Twilio.'
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from core.logging import get_logger
from monitoring.alerts.base import Alert, AlertChannel, AlertSeverity

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AlertRule:
    """One routing rule. ``predicate`` receives the alert and returns True if
    the rule matches. ``channels`` is the list of channel names to deliver to.
    """

    name: str
    predicate: Callable[[Alert], bool]
    channels: tuple[str, ...]


def by_min_severity(threshold: AlertSeverity) -> Callable[[Alert], bool]:
    """Match any alert at or above ``threshold``."""
    def _pred(alert: Alert) -> bool:
        return alert.severity.numeric >= threshold.numeric
    return _pred


def by_tag(key: str, value: str | None = None) -> Callable[[Alert], bool]:
    """Match when ``tag[key] == value`` (or just key present if value is None)."""
    def _pred(alert: Alert) -> bool:
        if key not in alert.tags:
            return False
        return value is None or alert.tags[key] == value
    return _pred


class AlertDispatcher:
    def __init__(
        self,
        channels: list[AlertChannel],
        rules: list[AlertRule],
        *,
        deduplicate_window_s: float = 30.0,
    ) -> None:
        self._channels: dict[str, AlertChannel] = {c.name: c for c in channels}
        if len(self._channels) != len(channels):
            raise ValueError("duplicate channel name")
        self._rules = list(rules)
        for r in self._rules:
            unknown = [c for c in r.channels if c not in self._channels]
            if unknown:
                raise ValueError(f"rule '{r.name}' references unknown channels: {unknown}")
        self._dedup_window = deduplicate_window_s
        self._recent: dict[str, float] = {}

    async def dispatch(self, alert: Alert) -> dict[str, bool]:
        """Send the alert to every channel that matches at least one rule.
        Returns ``{channel_name: success}``."""
        targets: set[str] = set()
        for rule in self._rules:
            if rule.predicate(alert):
                targets.update(rule.channels)

        if not targets:
            log.debug("alert_no_route", title=alert.title, severity=alert.severity.value)
            return {}

        # Dedup: same title within window suppressed.
        now = alert.timestamp.timestamp()
        last = self._recent.get(alert.title)
        if last is not None and (now - last) < self._dedup_window:
            log.debug("alert_dedup_suppressed", title=alert.title)
            return {}
        self._recent[alert.title] = now

        async def _send(channel_name: str) -> tuple[str, bool]:
            ch = self._channels[channel_name]
            try:
                await ch.send(alert)
                return channel_name, True
            except Exception as exc:
                log.exception("alert_channel_failed", channel=channel_name, error=str(exc))
                return channel_name, False

        results = await asyncio.gather(*[_send(n) for n in sorted(targets)])
        return dict(results)

    @property
    def channels(self) -> list[str]:
        return sorted(self._channels)

    @property
    def rules(self) -> list[AlertRule]:
        return list(self._rules)
