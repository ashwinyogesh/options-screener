# ADR-0014: Cost substitutions for the narrative platform ($150/mo budget)

- **Status**: Accepted
- **Date**: 2026-05-12

## Context

[ADR-0013](0013-narrative-intelligence-platform.md) commits to a Reddit narrative
intelligence platform on Azure with a hard ceiling of **$150/month** total Azure
spend. A "default" Azure reference architecture for an event-driven analytics
pipeline of this shape would land somewhere north of $400/mo:

| Default reference | Approx $/mo |
|---|---|
| Event Hubs Standard, 2 TU | ~$45 |
| Postgres GP_Standard_D4s_v3 | ~$210 |
| Azure Container Registry Basic | ~$5 |
| Azure ML workspace (compute idle) | ~$50 |
| polygon.io for price/options data | ~$29 |
| Redis Standard C1 | ~$55 |
| App Insights default ingest | ~$20+ |

That's ~3× over budget before a single OpenAI call. We have to make explicit,
deliberate substitutions and accept their tradeoffs.

## Options Considered

1. **Take the default reference, ask for a higher budget.**
   - Cons: defeats the project's framing (an opportunity-discovery layer, not a
     production trading system); kills the discipline that makes the rest of the
     architecture decisions tractable.
   - **Rejected.**

2. **Run everything on a single VM (Postgres + Python workers + Nginx).**
   - Cons: solves cost, recreates ops burden, eliminates managed reliability,
     conflicts with the existing App Service / Static Web Apps deployment shape.
   - **Rejected.**

3. **Azure-native, but rightsized aggressively per service. (Chosen.)**
   - Pros: keeps the managed-service model; documents *why* each cheaper SKU is
     acceptable so future contributors don't "fix" the savings; provides a
     ladder of cost-cut levers if MTD spend trends past $130.
   - Cons: every substitution carries a tradeoff (lower retention, fewer consumer
     groups, smaller Postgres tier). Those tradeoffs become live operational
     concerns under burst load.
   - **Accepted.**

## Decision

The substitution table below is binding. Any change requires a follow-up ADR with
explicit justification and an updated budget projection.

### Substitutions

| Capability | Default | Chosen | Saving | Tradeoff accepted |
|---|---|---|---|---|
| Event bus | Event Hubs Standard, 2 TU | **Event Hubs Basic, 1 TU** | ~$34 | 1-day retention; one consumer group per topic. Mitigated by Blob-first durability + secondary `ticker-events` topic for fanout. |
| Container registry | Azure Container Registry Basic | **GitHub Container Registry (ghcr.io)** | ~$5 | ACR-linked features (Tasks, geo-replication) unavailable. We don't need them. |
| Relational + vector + time-series | Postgres GP_Standard_D4s_v3 | **Postgres Flexible B1ms, 32 GiB Premium SSD** | ~$197 | Burstable CPU; 2 GiB RAM. Mitigated by cost-gated OpenAI calls, body-fingerprint dedup at insert time, hourly aggregation rather than streaming. |
| Cache | Redis Standard C1 | **Redis Basic C0, Phase 6 only** | ~$39 | No replication, no SLA. Phases 1–5 use Postgres + in-process LRU. |
| Pricing/options data | polygon.io | **yfinance (already in `backend/services/data_service.py`)** | ~$29 | Lower freshness; occasional gaps. Acceptable for a discovery layer; escalate only if Phase 6 backtest accuracy demands it. |
| Backtest compute | Azure ML workspace | **`scripts/backtest_narrative.py` + optional `job-backtest` (Container Apps Job)** | ~$50 | No experiment tracking UI. JSON metrics + small HTML report to Blob is enough at this stage. |
| Telemetry | App Insights default ingest | **App Insights Basic, 5 GB cap** | ~$10 | Sampling required at burst; we accept it. |
| Historical Reddit backfill | Pushshift / Arctic Shift | **None** | $0 | Pushshift is defunct (2023). Arctic Shift only revisited if Phase 5 narrative validation needs deep history. |

### Phase-staged cost (steady state)

| Service | SKU | $/mo |
|---|---|---|
| Event Hubs | Basic, 1 TU | ~$11 |
| Blob Storage | Standard_LRS, Hot, lifecycle | ~$3 |
| Postgres Flexible Server | B1ms, 32 GiB | ~$13 |
| Container Apps env (Consumption) | — | ~$5 |
| Container Apps Jobs | — | ~$5–10 |
| Key Vault | Standard | ~$1 |
| Azure OpenAI (gpt-4o-mini + text-embedding-3-small) | S0 | $20–40 var |
| App Insights | Basic, 5 GB cap | ~$10 |
| Redis Basic C0 (Phase 6) | — | ~$16 |
| Azure Budget, pg_cron, ghcr.io | — | $0 |
| **Baseline total** | | **~$85** |
| **Plus OpenAI variable** | | **+$20–40** |
| **Ceiling** | | **$150** |

### Cost guardrails

- Azure Budget alerts at **50% / 80% / 100%** of $150 → email + webhook.
- App Insights custom metric `azure_mtd_spend_usd` polled daily by a scheduled
  Container Apps Job (`job-cost-watch`).
- Weekly review: `az consumption usage list --start-date <Mon> --end-date <Sun>`.

### Cost-cut lever cascade

If MTD spend trends past $130 by mid-month, apply in order:

1. **Lever 1 (recommended first):** drop Event Hubs Basic and switch to
   **Storage Queues + Blob change-feed**. Saves ~$11/mo. Latency increases by
   roughly a minute per stage; acceptable for a discovery layer.
2. **Lever 2:** pause `job-classifier` and `job-narrative-detector`. Loses
   conviction freshness; saves the entire OpenAI variable. Existing rows keep
   serving.
3. **Lever 3:** scale Postgres to dev tier nightly via a scheduled Job. Saves
   ~$5/mo at the cost of Postgres being read-only during the off-window.

User-confirmed lever order: **Lever 1 first**.

## Consequences

- **Positive**: the platform fits the budget by design, not by hope. Cost-cut
  levers exist and are sequenced. Future contributors see *why* each cheaper SKU
  was chosen — the substitutions are explicit, not accidental.
- **Negative**: Event Hubs Basic's single consumer group per topic forces us to
  publish a second `ticker-events` topic for fanout instead of using consumer
  groups; Postgres B1ms will throttle under burst (CPI prints, FOMC) and we will
  have to throttle ingestion or accept lag; no Pushshift means Phase 5 narrative
  validation runs on going-forward data only.
- **Neutral**: tradeoffs are recoverable. If revenue or research value justifies
  it, every substitution can be reversed with a follow-up ADR.

## Follow-ups

- [ ] Wire Azure Budget alerts at $75 / $120 / $150 before Phase 1 deploy.
- [ ] Implement `job-cost-watch` (daily scheduled Container Apps Job) emitting the
      `azure_mtd_spend_usd` custom metric.
- [ ] Add a runbook entry to [docs/DEPLOYMENT.md](../DEPLOYMENT.md) for executing
      Lever 1 (Event Hubs → Storage Queues swap) end-to-end.
- [ ] Re-evaluate Postgres tier after Phase 6 if p99 latency targets miss.
