# Runbook — Deploying a new strategy

Promote a strategy from paper to live in stages. The hard guardrails
require explicit opt-in at every stage.

## Stages

```
DISABLED → backtest → paper → small-size live → full-size live
```

## 0. Backtest

  1. Write the strategy file in `strategies/<name>.py` + register via
     `@register("<name>")`.
  2. Add unit tests in `tests/unit/test_<name>.py`.
  3. Run a 5-year backtest:
     ```python
     from research.helpers import run_backtest_quick
     result = run_backtest_quick(strategy_name="<name>", parameters={...})
     ```
  4. Generate a tear sheet (`backtest.tearsheet.render_tearsheet`).
  5. **Acceptance criteria** before paper:
     - Sharpe ≥ 0.8 over 5 years
     - Max DD ≤ 15%
     - Win rate × avg-win ≥ avg-loss (positive expectancy)
     - No look-ahead bias (audited via code review)

## 1. Paper trading

  1. Add the strategy to `STRATEGIES_ENABLED` in `configmap.yaml`:
     ```yaml
     STRATEGIES_ENABLED: "sma_crossover,rsi_mean_reversion,<name>"
     ```
  2. Apply the change:
     ```bash
     kubectl -n algotrader apply -f deploy/k8s/configmap.yaml
     kubectl -n algotrader rollout restart deployment/live-engine
     ```
  3. The strategy starts with `Lifecycle.PAPER` and a paper Alpaca account.
  4. **Run for at least one week of market hours.**
  5. Daily checks:
     - Compare paper fills to expected from the backtest. Big divergence
       = bug.
     - Watch slippage metrics — backtest slippage was 5 bps, is reality
       worse?
     - Sniff the risk audit log for unexpected rejections.

## 2. Live trading — small size

  1. Add to the live allowlist:
     ```yaml
     STRATEGIES_LIVE_ALLOWED: "<name>"
     ```
  2. Set `LIVE=true` in the secret (if not already).
  3. Mark the strategy as `live: true` in its config (per-strategy file).
  4. **Constrain size aggressively** — temporarily tighten `MaxPositionSize`
     and `PerStrategyAllocation` to 1-2% for the strategy.
  5. Apply:
     ```bash
     kubectl -n algotrader apply -f deploy/k8s/{secrets,configmap}.yaml
     kubectl -n algotrader rollout restart deployment/live-engine
     ```
  6. **Run for one full month.** Watch every trade.

## 3. Full-size live

Only after a month of clean small-size trading. Loosen size caps gradually
(double weekly, not all at once).

## Rolling back

If the strategy is misbehaving live:

  1. Trip the kill switch immediately.
  2. Remove the strategy from `STRATEGIES_LIVE_ALLOWED`.
  3. Roll out the config change.
  4. Reset the kill switch.
  5. Investigate before re-enabling.
