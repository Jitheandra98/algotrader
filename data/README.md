# `data/` — Ingestion layer

## Components

```
data/
├── base.py                 # DataAdapter ABC, HistoricalBarRequest, FeedMode
├── ingestion.py            # IngestionPipeline: adapter → bus → aggregators → bus
├── writers.py              # TickWriter, BarWriter: bus → TimescaleDB
├── adapters/
│   ├── fixture.py          # FixtureAdapter — JSONL replay (deterministic, no network)
│   └── alpaca.py           # AlpacaAdapter — Alpaca paper/live (httpx + websockets)
└── aggregators/
    ├── base.py             # BarAggregator ABC
    ├── time_bars.py        # TimeBarAggregator (1s … 1w)
    ├── heikin_ashi.py      # HeikinAshiAggregator
    ├── renko.py            # RenkoAggregator (fixed brick size)
    └── range_bars.py       # RangeBarAggregator
```

## How data flows

```
Alpaca WS ─►─┐
             │ stream_ticks()
FixtureFile ─┤
             ▼
     ┌─────────────────────┐                    ┌──────────────────┐
     │  IngestionPipeline  │ ─► Stream.TICKS  ──┤ TickWriter       │ ─► Timescale.ticks
     │  - TimeBarAgg(M1)   │                    └──────────────────┘
     │  - TimeBarAgg(M5)   │                    ┌──────────────────┐
     │  - RenkoAgg, …      │ ─► Stream.bars.* ──┤ BarWriter        │ ─► Timescale.bars
     └─────────────────────┘                    └──────────────────┘
                                                ▲
                                                │ subscribe(group="writer-bars")
                          downstream feature engines / strategies subscribe
                          to the same streams with their own consumer groups
```

The point of decoupling at the bus: writers, features, and strategies all consume
the *same* bar stream as independent consumer groups. A slow writer won't back-press
a fast strategy.

## Adding a new broker adapter

1. Subclass `DataAdapter` in `data/adapters/<broker>.py`.
2. Implement `connect`, `close`, `historical_bars`, `stream_ticks`, `stream_bars`.
3. Use `EnvSecretProvider` (or whatever your config injects) to load credentials.
4. Normalize the broker's payloads into `core.schemas.Tick` / `core.schemas.Bar`
   — do **not** leak broker-specific types past the adapter boundary.
5. Add a record to the lazy `__getattr__` in `data/adapters/__init__.py` if the
   adapter has heavy dependencies you don't want loaded unconditionally.

## Aggregator semantics summary

| Aggregator   | Driven by | Bar timestamp     | Volume                 |
|--------------|-----------|-------------------|------------------------|
| TimeBar      | ticks     | bucket start (UTC) | Σ last_size in bucket |
| Heikin-Ashi  | bars      | source bar's ts   | passthrough            |
| Renko        | ticks     | closing tick ts   | tick size / N bricks  |
| Range bars   | ticks     | first tick ts     | Σ last_size in bar    |
