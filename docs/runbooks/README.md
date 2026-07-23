# Operator runbooks

When something goes wrong (or when you need to do something delicate), follow
the matching runbook. Each runbook is structured the same way:

  1. **Symptom** — what you'd see in the dashboard or an alert.
  2. **Triage** — quick checks to confirm the diagnosis.
  3. **Recovery** — step-by-step actions.
  4. **Postmortem prompts** — questions to answer before resuming.

| Scenario                           | Runbook |
|------------------------------------|---------|
| Broker disconnect                  | [broker-disconnect.md](broker-disconnect.md) |
| Drawdown breach                    | [drawdown-breach.md](drawdown-breach.md) |
| Stale data feed                    | [stale-data-feed.md](stale-data-feed.md) |
| Database outage                    | [db-outage.md](db-outage.md) |
| Kill switch tripped                | [kill-switch-tripped.md](kill-switch-tripped.md) |
| Deploying a new strategy           | [deploy-new-strategy.md](deploy-new-strategy.md) |
| Full disaster recovery             | [disaster-recovery.md](disaster-recovery.md) |

## Universal first steps

When *anything* goes wrong, before touching production:

  1. Check the operator UI (`/`). Is the kill switch tripped? Is health green?
  2. Skim recent risk audit decisions — are signals flowing through?
  3. Check Grafana dashboards for anomalies in latency / rejection rates.
  4. Read the most recent alerts in Slack / Discord. Don't act yet.

If the bot is exposed to material market risk and you're uncertain, **trip
the kill switch first**, then diagnose. Better to miss trades than make
wrong ones.
