# `journal/` — Trade journal

Per-trade record with the full provenance chain — signal rationale, risk
audit, indicator snapshot, fill quality, eventual P&L. Used for:

  - Post-trade review ("why did the bot do that?")
  - ML training-set assembly (M9 builds `Dataset` from `TradeJournal` + `FeatureStore`)
  - Compliance / tax reconstruction

## Recording a trade

The live engine (M7) calls `TradeJournal.record(...)` whenever a trade closes.
The entry captures everything needed to reconstruct *why* the trade happened:

```python
journal.record(JournalEntry(
    trade_id=str(trade.id),
    signal_id=str(originating_signal.id),
    strategy_id="rsi_mean_reversion",
    instrument="SPY|ETF",
    side="LONG",
    entry_time=trade.entry_time,
    exit_time=trade.exit_time,
    entry_price=trade.entry_price,
    exit_price=trade.exit_price,
    quantity=trade.quantity,
    pnl=trade.pnl,
    commission=trade.commission,
    rationale=originating_signal.rationale,
    risk_decision={"allowed": True, "rejections": []},
    indicator_snapshot={"rsi": 28.5, "atr": 1.2},
    fill_quality={"latency_ms": 80, "slippage_per_unit": "0.01"},
    tags={"session": "us-equity"},
))
```

## Storage

In-memory + optional JSONL append:

```python
journal = TradeJournal(jsonl_path="artifacts/journal.jsonl")
# ... record trades ...
print(journal.summary())
# {'n': 47, 'wins': 22, 'losses': 25, 'total_pnl': '345.12', 'win_rate': 0.468}
```

JSONL with one record per line lets you scroll through entries with `less` or
parse with any JSON-aware tool. Decimal values are preserved exactly (stored as
strings, parsed back to `Decimal` on load).

## Queries

The journal exposes narrow query methods only — anything richer goes through
the M10 DB layer:

```python
journal.by_strategy("rsi_mean_reversion")
journal.by_instrument("SPY|ETF")
journal.since(datetime(2026, 5, 1, tzinfo=UTC))
journal.summary()
```

## Privacy

If you ship a trade journal alongside model artifacts or research notebooks,
remember it contains:

  - Strategy parameters (in `rationale`)
  - Risk-engine internals (in `risk_decision`)
  - Indicator values you computed
  - Your fill quality (latency, slippage)

Treat the journal as confidential.
