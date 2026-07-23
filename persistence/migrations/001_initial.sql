-- 001_initial.sql
-- Bootstrap schema. Idempotent — safe to re-run.
--
-- Design choices:
--   * Time-series tables (ticks, bars, fills) are TimescaleDB hypertables, partitioned
--     by time. This gives us fast inserts and range scans without manual partitioning.
--   * Money is NUMERIC(20,8) — enough headroom for crypto and option premiums; the
--     Python side maps this to Decimal.
--   * All timestamps are TIMESTAMPTZ (UTC). The Python schema layer enforces this on
--     the way in.
--   * No deletions: trade history is append-only. Cleanup of stale ticks is a
--     TimescaleDB retention policy job (configured in a later migration).

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------- Reference data ----------

CREATE TABLE IF NOT EXISTS instruments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol          TEXT        NOT NULL,
    asset_class     TEXT        NOT NULL CHECK (asset_class IN
                        ('EQUITY','ETF','OPTION','FUTURE','FOREX','CRYPTO')),
    venue           TEXT,
    currency        CHAR(3)     NOT NULL DEFAULT 'USD',
    strike          NUMERIC(20,8),
    expiry          TIMESTAMPTZ,
    option_right    CHAR(1)     CHECK (option_right IN ('C','P')),
    contract_month  TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, asset_class, venue, expiry, strike, option_right, contract_month)
);

CREATE INDEX IF NOT EXISTS idx_instruments_symbol ON instruments (symbol);

-- ---------- Strategy config history ----------

CREATE TABLE IF NOT EXISTS strategy_config (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT        NOT NULL,
    version         INT         NOT NULL,
    enabled         BOOLEAN     NOT NULL,
    live            BOOLEAN     NOT NULL DEFAULT FALSE,
    params          JSONB       NOT NULL,
    activated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deactivated_at  TIMESTAMPTZ,
    UNIQUE (name, version)
);

CREATE INDEX IF NOT EXISTS idx_strategy_active ON strategy_config (name, deactivated_at);

-- ---------- Ticks (hypertable) ----------

CREATE TABLE IF NOT EXISTS ticks (
    ts          TIMESTAMPTZ NOT NULL,
    instrument  TEXT        NOT NULL,
    bid         NUMERIC(20,8),
    ask         NUMERIC(20,8),
    bid_size    NUMERIC(20,8),
    ask_size    NUMERIC(20,8),
    last_price  NUMERIC(20,8),
    last_size   NUMERIC(20,8),
    exchange    TEXT,
    conditions  TEXT[]
);

SELECT create_hypertable('ticks', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_ticks_instrument_ts ON ticks (instrument, ts DESC);

-- ---------- Bars (hypertable) ----------

CREATE TABLE IF NOT EXISTS bars (
    ts          TIMESTAMPTZ NOT NULL,
    instrument  TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL,
    open        NUMERIC(20,8) NOT NULL,
    high        NUMERIC(20,8) NOT NULL,
    low         NUMERIC(20,8) NOT NULL,
    close       NUMERIC(20,8) NOT NULL,
    volume      NUMERIC(20,8) NOT NULL,
    vwap        NUMERIC(20,8),
    trade_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument, timeframe, ts)
);

SELECT create_hypertable('bars', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_bars_instrument_tf_ts ON bars (instrument, timeframe, ts DESC);

-- ---------- Signals (append-only audit log) ----------

CREATE TABLE IF NOT EXISTS signals (
    id              UUID PRIMARY KEY,
    generated_at    TIMESTAMPTZ NOT NULL,
    strategy_id     TEXT        NOT NULL,
    instrument      TEXT        NOT NULL,
    direction       TEXT        NOT NULL,
    conviction      NUMERIC(5,4) NOT NULL,
    target_price    NUMERIC(20,8),
    stop_price      NUMERIC(20,8),
    timeframe       TEXT,
    rationale       TEXT        NOT NULL,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_signals_strategy_ts ON signals (strategy_id, generated_at DESC);

-- ---------- Risk decisions (append-only audit log) ----------

CREATE TABLE IF NOT EXISTS risk_decisions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_id       UUID        NOT NULL REFERENCES signals(id),
    allowed         BOOLEAN     NOT NULL,
    sized_quantity  NUMERIC(20,8),
    rejection_reason TEXT,
    constraints     JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_risk_decisions_signal ON risk_decisions (signal_id);

-- ---------- Orders ----------

CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY,
    client_order_id TEXT        NOT NULL UNIQUE,
    instrument      TEXT        NOT NULL,
    side            TEXT        NOT NULL,
    order_type      TEXT        NOT NULL,
    quantity        NUMERIC(20,8) NOT NULL,
    limit_price     NUMERIC(20,8),
    stop_price      NUMERIC(20,8),
    time_in_force   TEXT        NOT NULL,
    status          TEXT        NOT NULL,
    parent_order_id UUID,
    strategy_id     TEXT        NOT NULL,
    broker          TEXT        NOT NULL,
    broker_order_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders (strategy_id, created_at DESC);

-- ---------- Fills (hypertable) ----------

CREATE TABLE IF NOT EXISTS fills (
    id              UUID NOT NULL,
    order_id        UUID NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    instrument      TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        NUMERIC(20,8) NOT NULL,
    price           NUMERIC(20,8) NOT NULL,
    commission      NUMERIC(20,8) NOT NULL DEFAULT 0,
    broker_fill_id  TEXT,
    liquidity       CHAR(1),
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable('fills', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills (order_id);

-- ---------- Positions ----------

CREATE TABLE IF NOT EXISTS positions (
    instrument      TEXT        NOT NULL,
    strategy_id     TEXT,
    quantity        NUMERIC(20,8) NOT NULL,
    average_price   NUMERIC(20,8) NOT NULL,
    current_price   NUMERIC(20,8),
    unrealized_pnl  NUMERIC(20,8) NOT NULL DEFAULT 0,
    realized_pnl    NUMERIC(20,8) NOT NULL DEFAULT 0,
    opened_at       TIMESTAMPTZ NOT NULL,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument, strategy_id)
);

-- ---------- KPI snapshots ----------

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    ts              TIMESTAMPTZ NOT NULL,
    scope           TEXT        NOT NULL, -- 'portfolio', 'strategy:<name>', etc.
    equity          NUMERIC(20,8) NOT NULL,
    cash            NUMERIC(20,8) NOT NULL,
    realized_pnl    NUMERIC(20,8) NOT NULL,
    unrealized_pnl  NUMERIC(20,8) NOT NULL,
    drawdown        NUMERIC(20,8) NOT NULL,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (scope, ts)
);

SELECT create_hypertable('kpi_snapshots', 'ts', if_not_exists => TRUE);
