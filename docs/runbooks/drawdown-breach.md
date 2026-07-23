# Runbook — Drawdown breach

## Symptom

  - Alert: `[CRITICAL] drawdown circuit breaker tripped`
  - Kill switch is tripped with reason like
    `[drawdown] drawdown -7.20% breached cap -7.00%`
  - Operator UI: `kill-switch: TRIPPED`, `drawdown` widget shows breach
  - Metrics: `algotrader_kill_switch_tripped == 1`

## Triage

  1. Open `/` (operator UI) and review recent risk audit decisions and fills.
     Which strategy / instrument has the largest open loss?
  2. Check current open positions and their unrealized P&L. Are they
     mean-reverting or worsening?
  3. Cross-reference with market context — is this a broad-market drawdown
     or strategy-specific?

## Recovery

**Do not reset the kill switch until you understand the loss.** The breach
itself is a sign that something is off — either the strategy, the market
regime, or both.

### Triage decisions (pick ONE)

  - **Wait and hold**: keep positions open, kill switch on. New entries
    are blocked but existing positions can mark to market. If you expect
    a bounce, this is the lowest-friction option.
  - **Liquidate manually**: connect to the broker UI (Alpaca dashboard) and
    flatten positions yourself. The bot's kill switch prevents NEW entries,
    not exits — but manual broker action is the cleanest.
  - **Liquidate via the bot's exit signals**: this requires resetting the
    kill switch briefly. Risky; only do if you trust the strategy's exit
    logic. Re-trip immediately after exits fire.

### After recovery action

  1. Update strategy parameters if needed (e.g., tighten stop, reduce sizing).
  2. Verify Grafana shows current DD has recovered.
  3. Reset kill switch:
     ```
     POST /api/kill-switch/reset
     ```
  4. Watch the first 10 trades after reset closely.

## Postmortem prompts

  - Was the DD caused by one bad trade or a series?
  - Did the per-trade risk cap fire correctly? (Audit log should show it
    didn't reject — but if it did and the trade ran anyway, that's a P0.)
  - Was the breach within statistical expectation for the strategy's
    historical max DD?
  - Should the DD cap be lowered, or did the strategy structurally degrade?
