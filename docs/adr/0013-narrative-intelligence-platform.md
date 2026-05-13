# ADR-0013: Reddit narrative intelligence platform on Azure

- **Status**: Accepted
- **Date**: 2026-05-12

## Context

The existing Options Screener surfaces opportunities from technicals, options chain
math, and DCF anchors. None of those signals see a thesis until it is already priced
in: by the time relative strength turns or implied vol skew builds, the narrative is
mid-flight. We want a leading indicator — a system that observes *attention forming*
on Reddit and tells us when conviction is being built around a name **before**
institutional consensus.

The target is the first three stages of a narrative lifecycle:

1. niche technical discussion in specialist communities
2. early conviction formation (small contributor base, deep DD threads)
3. expanding awareness (cross-subreddit spread, contributor diversity rising)

Stages 4–6 (institutional attention, broad consensus, saturation) are explicitly
**out of scope**: by then the trade is consensus and the edge is gone.

This work integrates into the existing monorepo (FastAPI backend, React/Vite
frontend, Azure App Service + Static Web Apps) rather than standing up a parallel
product.

## Options Considered

1. **Buy a third-party sentiment feed (Stocktwits, Swaggy Stocks, etc.).**
    - Pros: zero build cost; immediate data.
    - Cons: someone else's model, scoring, and biases; we'd be paying for the
      lagging output of *their* lifecycle classifier; no ability to calibrate against
      our own scoring conventions; recurring cost violates the "no new top-level
      dependencies without justification" rule for an outsourced primitive.
    - **Rejected.**

2. **Self-hosted ELT pipeline on a single VM (Postgres + cron jobs).**
    - Pros: cheapest possible build; minimal Azure surface.
    - Cons: no horizontal scale on burst days (CPI prints, FOMC, earnings clusters);
      VM patching/lifecycle becomes a chore that doesn't exist with managed services;
      reliability is one disk failure away from data loss; doesn't compose with the
      existing App Service deployment model.
    - **Rejected.**

3. **Azure-native event-driven pipeline, rightsized to a hard $150/mo budget.
   (Chosen.)**
    - Pros: managed services for the things we don't want to operate (Postgres,
      Event Hubs, Blob, Key Vault); scale-to-zero batch workers via Container Apps
      Jobs keep idle cost flat; integrates cleanly with the existing App Service
      backend (same subscription, same region, same identity model).
    - Cons: requires explicit cost discipline at every architectural choice; some
      "default" Azure picks (Standard Event Hubs, dedicated Postgres tier, Azure
      Container Registry, Azure ML) had to be substituted for cheaper alternatives.
      Those substitutions are documented in [ADR-0014](0014-narrative-cost-substitutions.md).
    - **Accepted.**

## Decision

Build a five-stage attention pipeline on Azure-native services in `centralus`,
deployed via Bicep and GitHub Actions, integrated into the existing App Service
backend and Static Web Apps frontend.

```
Reddit (PRAW)
  → ca-ingestion       (always-on Container App, MinReplicas=1)
  → Blob (durability)
  → Event Hubs Basic (reddit-raw-events)
  → job-extractor      (Container Apps Job, cron)
  → Postgres ticker_events + Event Hubs ticker-events
  → job-aggregator     (15-min cron)  → ticker_timeline (TimescaleDB hypertable)
  → job-classifier     (30-min cron)  → conviction state via gpt-4o-mini
  → job-narrative-detector (hourly)   → HDBSCAN clusters + lifecycle stage
  → job-acs-scorer     (15-min cron)  → acs_scores
  → App Service        (existing)     → /api/narrative/* routes
  → Static Web Apps    (existing)     → Narrative tab
```

### Key architectural commitments

- **Blob is the durable source of truth.** Event Hubs Basic has 1-day retention; if
  any consumer falls behind, replay from Blob. Ingestion writes Blob *before*
  publishing to Event Hubs.
- **One OpenAI round-trip per post.** Embedding generation (`text-embedding-3-small`)
  and conviction classification (`gpt-4o-mini`) batch together. Layer-3 ticker
  disambiguation is **cost-gated**: only invoked when the candidate is a Layer-2
  medium-confidence match AND post.score > 5 AND author_weight ≥ 0.5.
- **Postgres is the single relational + vector + time-series store.** Flexible
  Server B1ms with `pgvector`, `timescaledb`, `pg_cron`, `uuid-ossp` extensions
  installed at provisioning. No separate vector DB, no separate time-series DB.
- **Author privacy is non-negotiable.** Usernames are hashed with a Key Vault salt
  before any persistence. Required for Reddit API ToS compliance post-2023.
- **Routers/services layering carries over.** New `backend/routers/narrative.py`
  delegates to `backend/services/narrative/*`; services don't import FastAPI types.
  Database access goes through `backend/services/narrative_db.py` (asyncpg pool).
- **Methodology and code stay in lockstep.** Scoring math, lifecycle thresholds,
  and ACS weights live in [docs/NARRATIVE_METHODOLOGY.md](../NARRATIVE_METHODOLOGY.md);
  changes require simultaneous code + doc updates per the standing repo rule.
- **ACS weights are runtime configuration.** The scorer reads `acs-component-weights`
  from Key Vault at Job startup. Calibration changes do not require redeploy.

### Phasing

Phases 0–6 in [docs/NARRATIVE_METHODOLOGY.md](../NARRATIVE_METHODOLOGY.md#phasing).
Phase 0 is repository scaffolding only; no live Azure resources are provisioned
until Phase 1 lands the Bicep stack and CI/CD.

### Hard targets

- Steady-state Azure cost: $80–110 baseline + $20–40 OpenAI variable, ceiling $150.
- Layer-2 ticker extraction precision ≥ 0.92 (high) / ≥ 0.80 (medium).
- Conviction classifier F1 ≥ 0.78 on `researched_bull` against a 300-post eval set.
- Backtest IC ≥ 0.04 at T+30 for stage-2 ACS signals.
- API: `GET /api/narrative/tickers/top` p99 < 200ms with Redis warm.
- Freshness: `acs_staleness_seconds` < 900 for ≥99% of any 24h window.

## Consequences

- **Positive**: a leading-indicator data layer that is composable with the existing
  screener verdicts; managed services keep operational load near zero; the entire
  scoring rationale is human-auditable in one methodology doc; cost is bounded by
  construction.
- **Negative**: ~9 new top-level deps across worker images (PRAW, asyncpg,
  azure-eventhub, azure-storage-blob, openai, hdbscan, scikit-learn, scipy, numpy);
  six new container images to build and ship; new infra surface (Bicep + GitHub
  Actions) the team has to learn.
- **Neutral**: introduces a `workers/` directory at the repo root, separate from
  `backend/`. Worker images do not share the FastAPI dependency closure; each has
  its own pinned `requirements.txt`.

## Follow-ups

- [ ] Phase 1: provision Bicep stack (Event Hubs Basic, Blob, Key Vault, Container
      Apps env, Postgres B1ms, App Insights).
- [ ] Phase 1: ship `ca-ingestion` worker with PRAW + Blob-first + Event Hubs
      publish.
- [ ] Phase 2: ship `job-extractor` with Layer 1–5 extraction.
- [ ] Phase 2: hand-label 500 mentions for precision evaluation.
- [ ] Phase 4: confirm Azure OpenAI quota in `centralus` (`gpt-4o-mini`,
      `text-embedding-3-small`).
- [ ] Phase 6: introduce Redis Basic C0; expose `/api/narrative/*` and Narrative
      tab.
- [ ] Phase 6: backtest IC validation, weight calibration sync to Key Vault.
- [ ] Set Azure Budget alerts at $75 / $120 / $150 before Phase 1 deploy.
