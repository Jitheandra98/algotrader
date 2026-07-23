"""Trade journal — per-trade rationale capture.

Records for every trade: signal rationale, risk-decision audit, indicator
snapshot at entry, fill quality, eventual P&L. Used for post-trade review,
ML training-set assembly, and tax/compliance reconstruction.
"""

from journal.store import JournalEntry, TradeJournal

__all__ = ["JournalEntry", "TradeJournal"]
