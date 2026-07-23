"""Alert dispatcher routing + channel tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from monitoring.alerts import (
    Alert,
    AlertChannel,
    AlertDispatcher,
    AlertRule,
    AlertSeverity,
)
from monitoring.alerts.discord import DiscordChannel
from monitoring.alerts.dispatcher import by_min_severity, by_tag
from monitoring.alerts.slack import SlackChannel

UTC = timezone.utc


class RecordingChannel(AlertChannel):
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.received: list[Alert] = []
        self._fail = fail

    async def send(self, alert: Alert) -> None:
        if self._fail:
            raise RuntimeError("test channel failure")
        self.received.append(alert)


@pytest.mark.asyncio
async def test_dispatcher_routes_by_severity() -> None:
    slack = RecordingChannel("slack")
    sms = RecordingChannel("sms")
    d = AlertDispatcher(
        channels=[slack, sms],
        rules=[
            AlertRule("info-to-slack", by_min_severity(AlertSeverity.INFO), ("slack",)),
            AlertRule("critical-everywhere", by_min_severity(AlertSeverity.CRITICAL), ("slack", "sms")),
        ],
    )
    await d.dispatch(Alert(title="x", body="b", severity=AlertSeverity.INFO))
    assert len(slack.received) == 1
    assert len(sms.received) == 0

    await d.dispatch(Alert(title="y", body="b", severity=AlertSeverity.CRITICAL))
    # 2 distinct alerts in both rules' channels.
    assert len(slack.received) == 2
    assert len(sms.received) == 1


@pytest.mark.asyncio
async def test_dispatcher_by_tag() -> None:
    risk_ch = RecordingChannel("risk-ch")
    d = AlertDispatcher(
        channels=[risk_ch],
        rules=[AlertRule("risk-alerts", by_tag("type", "risk"), ("risk-ch",))],
    )
    await d.dispatch(Alert(title="ok", body="b", tags={"type": "info"}))
    assert risk_ch.received == []
    await d.dispatch(Alert(title="bad", body="b", tags={"type": "risk"}))
    assert len(risk_ch.received) == 1


@pytest.mark.asyncio
async def test_dispatcher_deduplicates_within_window() -> None:
    ch = RecordingChannel("ch")
    d = AlertDispatcher(
        channels=[ch],
        rules=[AlertRule("all", lambda _: True, ("ch",))],
        deduplicate_window_s=60.0,
    )
    a = Alert(title="repeat", body="b")
    await d.dispatch(a)
    await d.dispatch(a)  # same title → suppressed
    assert len(ch.received) == 1


@pytest.mark.asyncio
async def test_dispatcher_one_channel_failure_doesnt_block_others() -> None:
    good = RecordingChannel("good")
    bad = RecordingChannel("bad", fail=True)
    d = AlertDispatcher(
        channels=[good, bad],
        rules=[AlertRule("both", lambda _: True, ("good", "bad"))],
    )
    results = await d.dispatch(Alert(title="t", body="b"))
    assert results == {"good": True, "bad": False}
    assert good.received != []


def test_unknown_channel_in_rule_rejected() -> None:
    good = RecordingChannel("good")
    with pytest.raises(ValueError, match="unknown channels"):
        AlertDispatcher(
            channels=[good],
            rules=[AlertRule("r", lambda _: True, ("ghost",))],
        )


def test_duplicate_channel_name_rejected() -> None:
    a = RecordingChannel("x")
    b = RecordingChannel("x")
    with pytest.raises(ValueError, match="duplicate"):
        AlertDispatcher(channels=[a, b], rules=[])


def test_slack_payload_shape() -> None:
    ch = SlackChannel(webhook_url="https://hooks.slack.com/services/test")
    payload = ch.build_payload(Alert(title="t", body="b", severity=AlertSeverity.ERROR, tags={"k": "v"}))
    assert "attachments" in payload
    att = payload["attachments"][0]
    assert att["title"].startswith("[ERROR]")
    assert att["color"] == "#FF5722"
    assert {"title": "k", "value": "v", "short": True} in att["fields"]


def test_discord_payload_shape() -> None:
    ch = DiscordChannel(webhook_url="https://discord.com/api/webhooks/x/y")
    payload = ch.build_payload(Alert(title="t", body="b", severity=AlertSeverity.WARNING, tags={"a": "b"}))
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert embed["title"].startswith("[WARNING]")
