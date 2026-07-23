"""Alert types + channel ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

UTC = timezone.utc


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def numeric(self) -> int:
        order = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}
        return order[self.value]


@dataclass(frozen=True, slots=True)
class Alert:
    title: str
    body: str
    severity: AlertSeverity = AlertSeverity.WARNING
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        return f"[{self.severity.value}] {self.title}"


class AlertChannel(ABC):
    """One delivery target. Sends alerts independently — failures don't block other channels."""

    name: str

    @abstractmethod
    async def send(self, alert: Alert) -> None: ...
