"""Signal filters — the 'ensemble' / 'confirmation' layer.

A :class:`SignalFilter` sits between strategy signal emission and risk/order
conversion. Filters can:
  - drop signals (low conviction, no confirmation)
  - combine signals into a single trade decision
  - require N strategies to agree before a signal is promoted

The engine applies filters in order. Each filter takes the current list and
returns a (possibly smaller, possibly transformed) list.

Filters are stateful when needed (e.g., for time-window confirmation) and
deterministic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from core.schemas import Direction, Signal


class SignalFilter(ABC):
    """Stateful or stateless transform on a list of Signals."""

    @abstractmethod
    def filter(self, signals: Sequence[Signal], *, now: datetime) -> list[Signal]:
        """Return the (transformed) signals that should pass through to risk."""


class MinConvictionFilter(SignalFilter):
    """Drop signals whose conviction is below ``threshold``."""

    def __init__(self, threshold: float = 0.5) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be in [0,1]")
        self._t = threshold

    def filter(self, signals: Sequence[Signal], *, now: datetime) -> list[Signal]:  # noqa: ARG002
        return [s for s in signals if s.conviction >= self._t]


@dataclass(slots=True)
class _Vote:
    timestamp: datetime
    conviction: float
    strategy_id: str


class ConfirmationFilter(SignalFilter):
    """Require ``min_votes`` independent strategies to emit a signal on the same
    ``(instrument, direction)`` within ``window`` time. Output is a single
    consolidated signal whose ``conviction`` is the average of the votes and
    whose ``rationale`` lists the contributing strategies.

    State is per ``(instrument, direction)``. Votes outside the window expire.
    """

    def __init__(
        self,
        *,
        min_votes: int = 2,
        window: timedelta = timedelta(minutes=5),
    ) -> None:
        if min_votes < 1:
            raise ValueError("min_votes must be >= 1")
        self._min = min_votes
        self._window = window
        self._votes: dict[tuple[str, Direction], list[_Vote]] = defaultdict(list)
        # Track which (instrument, direction) keys have already been promoted in
        # the current window to avoid double-firing on every subsequent vote.
        self._promoted: dict[tuple[str, Direction], datetime] = {}

    def filter(self, signals: Sequence[Signal], *, now: datetime) -> list[Signal]:
        out: list[Signal] = []
        # Expire old votes.
        cutoff = now - self._window
        for k, votes in list(self._votes.items()):
            self._votes[k] = [v for v in votes if v.timestamp >= cutoff]
            if not self._votes[k]:
                self._votes.pop(k)
        # Clear stale promotions.
        for k, ts in list(self._promoted.items()):
            if ts < cutoff:
                self._promoted.pop(k)

        # HOLD signals fall through unchanged (they're informational only and
        # don't consume sizing).
        passthrough = [s for s in signals if s.direction is Direction.HOLD]

        # Tally new votes.
        for s in signals:
            if s.direction is Direction.HOLD:
                continue
            key = (s.instrument.key, s.direction)
            # De-dupe by strategy id within the window.
            self._votes[key] = [v for v in self._votes[key] if v.strategy_id != s.strategy_id]
            self._votes[key].append(_Vote(timestamp=now, conviction=s.conviction, strategy_id=s.strategy_id))
            if len(self._votes[key]) >= self._min and key not in self._promoted:
                votes = self._votes[key]
                avg_conv = sum(v.conviction for v in votes) / len(votes)
                rationale = (
                    f"confirmed by {len(votes)} strategies: "
                    + ", ".join(sorted({v.strategy_id for v in votes}))
                )
                # Emit one consolidated signal. The "strategy_id" becomes the
                # ensemble's name so downstream order routing can identify it.
                out.append(
                    Signal(
                        id=uuid4(),
                        strategy_id="ensemble",
                        instrument=s.instrument,
                        direction=s.direction,
                        conviction=min(1.0, avg_conv),
                        target_price=s.target_price,
                        stop_price=s.stop_price,
                        timeframe=s.timeframe,
                        rationale=rationale,
                        metadata={
                            "contributors": sorted({v.strategy_id for v in votes}),
                            "votes": len(votes),
                        },
                    )
                )
                self._promoted[key] = now

        return passthrough + out


class StopOpposingSignalsFilter(SignalFilter):
    """When LONG and SHORT signals appear for the same instrument in the same
    batch, drop both. They cancel out — somebody's wrong."""

    def filter(self, signals: Sequence[Signal], *, now: datetime) -> list[Signal]:  # noqa: ARG002
        by_inst: dict[str, set[Direction]] = defaultdict(set)
        for s in signals:
            by_inst[s.instrument.key].add(s.direction)
        return [
            s
            for s in signals
            if not (Direction.LONG in by_inst[s.instrument.key] and Direction.SHORT in by_inst[s.instrument.key])
        ]
