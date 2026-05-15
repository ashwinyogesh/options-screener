# Narrative Intelligence Platform — System Design

## What and why

This document describes the end-to-end architecture of the Narrative Intelligence
platform: the data flow, the responsibility of each component, and the design
rationale behind each boundary. It is the **"how it works"** complement to
[NARRATIVE_METHODOLOGY.md](NARRATIVE_METHODOLOGY.md), which is the **"what we
measure and why we score it"** reference.

Audience: a contributor asking "where does this data come from, where does it go,
and why is it split this way?" If you want the scoring math, go to the methodology
doc. If you want the infra provisioning, go to `infra/`.

## Section index

1. [One-page overview](#1-one-page-overview)
2. [Data flow diagram](#2-data-flow-diagram)
3. [Phase 1 — Ingestion](#3-phase-1--ingestion)
4. [Phase 2 — Extraction](#4-phase-2--extraction)
5. [Phase 3 — Aggregation](#5-phase-3--aggregation)
6. [Phase 4 + 5 — Classification and embedding](#6-phase-4--5--classification-and-embedding)
7. [Phase 5 — Narrative detection](#7-phase-5--narrative-detection)
8. [Phase 6 — ACS scoring](#8-phase-6--acs-scoring)
9. [Read path — FastAPI + frontend](#9-read-path--fastapi--frontend)
10. [Azure infrastructure map](#10-azure-infrastructure-map)
11. [Inter-component contracts](#11-inter-component-contracts)
12. [Failure and recovery](#12-failure-and-recovery)
13. [Cosmos DB schema reference](#13-cosmos-db-schema-reference)

---

## 1. One-page overview

```
Reddit (17 subreddits, 3 tiers)
      │  Arctic Shift API  (60s poll, 6h look-back)
      ▼
┌─────────────────┐    Blob Storage
│  job-ingestor   │ ──────────────────► reddit-raw/ (durable backup)
│  (always-on)    │
└────────┬────────┘
         │  Event Hubs  (reddit-raw-events)
         ▼
┌─────────────────┐
│ job-extractor   │  GPT-4o-mini: "what tickers with what sentiment?"
│ (5-min cron)    │
└────────┬────────┘
         │  Cosmos DB  (signals container)
         ▼
┌─────────────────┐
│ job-aggregator  │  §2 attention math: decay, Gini, acceleration, depth
│ (15-min cron)   │
└────────┬────────┘
         │  Cosmos DB  (ticker_timeline, attention fields)
         ├─────────────────────────────────────────────┐
         ▼                                             ▼
┌─────────────────┐                        ┌─────────────────────┐
│ job-classifier  │  GPT-4o-mini:          │ job-narrative-      │
│ (30-min cron)   │  conviction state +    │ detector            │
│                 │  embedding             │ (hourly cron)       │
└────────┬────────┘                        └──────────┬──────────┘
         │  signals.conviction_state                  │  ticker_timeline
         │  signals.embedding                         │  lifecycle_stage
         │  (written back to signals)                 │  stage_confidence
         └──────────────┬─────────────────────────────┘
                        ▼
              ┌─────────────────┐
              │ job-acs-scorer  │  §5 ACS formula: A+B+C+D  (E=0, Phase 6.1)
              │ (20-min cron)   │  + haircuts + CI bootstrap
              └────────┬────────┘
                       │  ticker_timeline.acs / acs_ci_lower / acs_ci_upper
                       ▼
              ┌─────────────────┐
              │  FastAPI        │  /api/narrative/*  (read-only, direct Cosmos)
              │  backend        │
              └────────┬────────┘
                       │  JSON
                       ▼
              ┌─────────────────┐
              │  React frontend │  Narrative tab (gated by VITE_NARRATIVE_ENABLED)
              └─────────────────┘
```

---

## 2. Data flow diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ WRITE PATH (workers — Azure Container Apps Jobs + one always-on App)         │
│                                                                              │
│  Arctic Shift ──► job-ingestor ──► Event Hubs ──► job-extractor             │
│                         │                               │                   │
│                    Blob Storage                    signals (Cosmos)          │
│                   (audit trail)                         │                   │
│                                              ┌──────────┴──────────┐        │
│                                              ▼                     ▼        │
│                                       job-aggregator         job-classifier │
│                                              │                     │        │
│                                    ticker_timeline         signals.embedding │
│                                    (attention fields)      signals.conviction│
│                                              │                     │        │
│                                              └──────────┬──────────┘        │
│                                                         ▼                   │
│                                               job-narrative-detector        │
│                                                         │                   │
│                                              ticker_timeline.lifecycle_stage │
│                                                         │                   │
│                                                         ▼                   │
│                                                  job-acs-scorer             │
│                                                         │                   │
│                                              ticker_timeline.acs            │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ READ PATH (request-time — FastAPI + Static Web Apps)                         │
│                                                                              │
│  Browser ──► React (Narrative tab) ──► FastAPI /api/narrative/*             │
│                                               │                              │
│                                        Cosmos ticker_timeline               │
│                                        (direct read, no Redis)              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

The write path and read path share **only the `ticker_timeline` Cosmos
container**. All workers are independently deployable; no worker calls another
worker's HTTP API. The FastAPI backend is strictly read-only against Cosmos.

---

## 3. Phase 1 — Ingestion

### What

`job-ingestor` is an **always-on** Container App (MinReplicas=1, MaxReplicas=2).
It polls the Arctic Shift Reddit archive API across 17 subreddits every 60 seconds,
writes each batch to **Blob Storage first** (durability), then publishes to **Event
Hubs** (delivery). Posts older than 36 hours are published as-is; scores for newer
posts are frozen at 1 by Arctic Shift's archival lag.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  job-ingestor (always-on, 60s poll loop)                                 │
│                                                                          │
│  ┌────────────────┐   per subreddit (17 total, 3 tiers):                 │
│  │  Arctic Shift  │   - tier1: investing, stocks, SecurityAnalysis,      │
│  │  API           │            ValueInvesting, Bogleheads                │
│  │  (public, no   │   - tier2: wallstreetbets, options, pennystocks, ... │
│  │   auth)        │   - tier3: artificial, SemiConductors, biotech, ...  │
│  └───────┬────────┘                                                      │
│          │  GET /posts?after=now-6h&sort=desc&limit=100                  │
│          │  GET /comments?...                                            │
│          ▼                                                               │
│  ┌────────────────┐   SHA-256(username + KV salt) → authorHash          │
│  │  RawEvent      │   body, title, flair, createdUtc, subreddit          │
│  │  schema        │   post_id, source="reddit_json"                      │
│  └───────┬────────┘                                                      │
│          │                                                               │
│     ┌────┴─────┐                                                         │
│     ▼          ▼                                                         │
│  Blob       Event Hubs                                                   │
│  reddit-raw/ reddit-raw-events                                           │
│  (durable)  (delivery, 1-day retention)                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

### How

- **6h look-back window** (not a cursor): every poll call passes
  `after = now - 21600s`. Arctic Shift returns the 100 newest posts in that
  window per subreddit. The same post is re-published on every poll cycle for
  up to 6 hours.
- **Blob-first**: write to Blob, then publish to EH. If EH publish fails, the
  data is durable on Blob for manual replay. Blobs that fail EH publish are
  never deleted.
- **Author hashing**: raw Reddit usernames are never persisted. `SHA-256(username
  + KV salt)` is used so downstream dedup still works across poll cycles for
  the same author.

### Why these choices

| Decision | Rationale |
|---|---|
| Arctic Shift over Reddit OAuth | No app registration, no OAuth flow, works from Azure IPs, returns full selftext. The only trade-off is archival score lag (< 36h = score 1), which is acceptable because we don't gate on score in Phase 2. |
| 6h look-back (not a cursor) | Arctic Shift indexes posts non-uniformly — a cursor-advance permanently misses late-indexed posts. The 6h window catches them. Cosmos upsert deduplicates downstream. |
| Blob + EH (not EH-only) | EH Basic SKU has 1-day retention and no consumer group replay. Blob is the durable audit trail and replay source if EH is exhausted. |
| Always-on (not cron) | Latency matters: a cron poll every 5 min adds ≥2.5 min average staleness. A 60s always-on loop keeps signal latency under 2 min. |

---

## 4. Phase 2 — Extraction

### What

`job-extractor` is a **5-minute cron** Container Apps Job. It consumes up to 40
events per run from Event Hubs, runs a pre-flight dedup check against Cosmos, then
calls GPT-4o-mini to extract structured `(ticker, sentiment, confidence, rationale)`
tuples from post bodies. Extracted signals are written to the `signals` Cosmos
container.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  job-extractor (5-min cron, replicaTimeout=300s)                         │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  1. EH receive window (25s)                                     │     │
│  │     - starting_position=@latest (steady-state)                  │     │
│  │     - collect up to MAX_EVENTS_PER_RUN=40 events                │     │
│  │     - checkpoint each event offset (no re-processing)           │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  2. Pre-flight dedup                                            │     │
│  │     SELECT DISTINCT VALUE c.postId FROM c                       │     │
│  │     WHERE c.postId IN (@p0, @p1, ...)                           │     │
│  │     → skip already-extracted post_ids entirely                  │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  3. Layer 1 cost gate                                           │     │
│  │     skip posts where len(body) < 20 chars                       │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  4. OpenAI extraction (1.25s throttle between calls, ≤48 RPM)   │     │
│  │     GPT-4o-mini → {"signals": [                                 │     │
│  │       {"ticker":"NVDA","sentiment":"bullish",                   │     │
│  │        "confidence":0.9,"rationale":"..."}                      │     │
│  │     ]}                                                          │     │
│  │     Retry: RateLimitError / APIConnectionError only (3×, 15-60s)│     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  5. Cosmos upsert (backstop dedup)                              │     │
│  │     id = f"{post_id}_{ticker}"                                  │     │
│  │     partition key = ticker                                       │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘

Output document shape (signals container):
  id, ticker, sentiment, confidence, rationale,
  postId, subreddit, flair, authorHash, createdUtc,
  source, extractedAt
  [conviction_state, embedding added by Phase 4/5 classifier]
```

### How

- **Two-layer dedup**: (a) pre-flight Cosmos query skips posts already in
  `signals` before calling OpenAI; (b) `upsert_item(id=post_id_ticker)` ensures
  no duplicate documents even if (a) misses something.
- **EH checkpoint**: `partition_context.update_checkpoint(event)` advances the
  consumer group offset per event. Re-running the job after a crash won't
  re-process already-checkpointed events.
- **RPM throttle**: 1.25s `time.sleep` between OpenAI calls = 48 RPM, staying
  under the 50 RPM Azure OpenAI quota.

### Why these choices

| Decision | Rationale |
|---|---|
| MAX_EVENTS_PER_RUN=40 | 40 × 1.25s throttle + 25s receive window + 15s cold start = ~90s — well under the 300s replicaTimeout. 500 (old default) needed 10 min against 50 RPM quota, always hitting DeadlineExceeded. |
| Pre-flight dedup (not just upsert backstop) | The 6h look-back re-publishes the same posts on every 5-min cycle. Without pre-flight, every run wastes ~40 × 50 RPM quota on already-extracted posts. |
| @latest EH position (not -1) | Prevents replaying the full 1-day EH retention window on every restart. Set EXTRACTOR_REPLAY_FROM_START=true once for initial catch-up, then revert. |
| Retry only transient OpenAI errors | Retrying bare Exception masks programming bugs. RateLimitError + APIConnectionError + APITimeoutError are the only errors worth retrying. |

---

## 5. Phase 3 — Aggregation

### What

`job-aggregator` is a **15-minute cron** Container Apps Job. For every ticker
active in the last 30 days, it reads all signals from Cosmos and computes the
§2 attention dimensions, writing a `TickerTimelineSnapshot` to the
`ticker_timeline` container. It is **stateless and idempotent**: each run reads
from `signals` and fully recomputes from scratch.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  job-aggregator (15-min cron)                                            │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  1. Distinct tickers in last 30 days                            │     │
│  │     SELECT DISTINCT VALUE c.ticker FROM c                       │     │
│  │     WHERE c.createdUtc >= cutoff                                │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          │  for each ticker:                            │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  2. Fetch all 30d signals for ticker (ordered ASC by createdUtc)│     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  3. build_snapshot() — pure functions in attention.py            │    │
│  │                                                                  │    │
│  │  §2.1 Persistence                                                │    │
│  │    decay_weighted_density(signals, window=14d, λ=0.1)           │    │
│  │    decay_weighted_density(signals, window=7d, λ=0.1)            │    │
│  │    decay_weighted_density(signals, window=30d, λ=0.1)           │    │
│  │    daily_buckets[]  ← used by scorer's CI bootstrap             │    │
│  │                                                                  │    │
│  │  §2.2 Acceleration                                               │    │
│  │    accel = (dwd_7d - dwd_30d) / dwd_30d                         │    │
│  │                                                                  │    │
│  │  §2.3 Contributor diversity (14d window only)                    │    │
│  │    unique_authors_14d                                            │    │
│  │    gini_14d  ← per-author signal counts scoped to 14d           │    │
│  │    contributor_count_growth_7d  ← stage 3 proxy                 │    │
│  │                                                                  │    │
│  │  §2.4 Discussion depth (14d window)                              │    │
│  │    dd_post_ratio  ← flair / body DD keyword match               │    │
│  │    financial_term_density  ← _FINANCIAL_TERMS substring match   │    │
│  │    avg_body_len  ← display only, not scored                     │    │
│  │                                                                  │    │
│  │  §2.5 Composite                                                  │    │
│  │    attention_quality = 0.35·P + 0.25·D + 0.25·Dp + 0.15·A     │    │
│  │                                                                  │    │
│  │  §3 Conviction rollup (if classifier has run)                   │    │
│  │    conviction_researched_bull_ratio                              │    │
│  │    conviction_researched_bear_ratio                              │    │
│  │    conviction_emotional_bull_ratio                               │    │
│  │    conviction_dd_norm  ← weighted mean of §3 state weights      │    │
│  │                                                                  │    │
│  │  Tier pcts                                                       │    │
│  │    tier1_pct, tier2_pct, tier3_pct  ← subreddit tier fractions  │    │
│  └───────────────────────┬─────────────────────────────────────────┘    │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  4. upsert ticker_timeline  id = f"{ticker}_{bucket_date}"      │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

### How

Full recompute every run — no incremental state. This is correct because:
- Decay-weighted density, Gini, and acceleration are all non-additive; you
  cannot cheaply update them without the full window.
- Late-arriving signals (from the 6h look-back re-publishing older posts) are
  automatically included on the next run.
- Idempotent upsert means re-running the same day is always safe.

### Why these choices

| Decision | Rationale |
|---|---|
| Full recompute (not delta) | Window-based metrics (Gini, decay-weighted density) require the full signal set to be correct. At current scale (Cosmos Serverless, per-RU billing, small signal counts) the cost is negligible. |
| id = ticker_bucket_date | One document per ticker per day. Today's document is overwritten on every 15-min run with refreshed metrics. History is preserved as prior-day documents. |
| Pure functions in attention.py | Side-effect-free, fully unit-testable without Cosmos. The same module is mirrored in `backend/services/narrative/attention.py` for the FastAPI read path. |
| 15-min cadence | Matches the ACS scorer cadence. Scorer always reads freshly aggregated metrics. |

---

## 6. Phase 4 + 5 — Classification and embedding

### What

`job-classifier` is a **30-minute cron** Container Apps Job. It processes
unclassified signals from Cosmos — those without a `conviction_state` field —
and makes **two OpenAI calls per signal**: one for conviction classification
(GPT-4o-mini, structured output) and one for embedding generation
(`text-embedding-ada-002`, 1536-dim). Both are written back to the signal
document in a single upsert. An embedding failure does not block the
conviction state write.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  job-classifier (30-min cron)                                            │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  1. Fetch unclassified signals (batches of 50, up to 200/run)   │     │
│  │     SELECT ... FROM c                                           │     │
│  │     WHERE NOT IS_DEFINED(c.conviction_state)                    │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          │  for each signal:                            │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  2a. GPT-4o-mini conviction classification                      │     │
│  │      Prompt (stored in KV as conviction-prompt-v1):             │     │
│  │      "classify this text into one of 10 states..."              │     │
│  │      → conviction_state, conviction_confidence                  │     │
│  │                                                                  │     │
│  │      10 states (§3):                                            │     │
│  │      researched_bull (1.0) | researched_bear (1.0)              │     │
│  │      emotional_bull (0.4)  | emotional_bear (0.4)               │     │
│  │      uncertainty (0.0)     | earnings_focused (0.8)             │     │
│  │      product_thesis (0.8)  | ecosystem_thesis (0.8)             │     │
│  │      institutional_watch (0.9) | exit_signal (-0.5)             │     │
│  └─────────────────┬───────────────────────────────────────────────┘     │
│                    │  (soft-fail: embedding error ≠ block conviction)    │
│                    ▼                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  2b. text-embedding-ada-002 (1536-dim)                          │     │
│  │      input: signal.rationale text                               │     │
│  │      → embedding float[1536]                                    │     │
│  │      failure → embedding=null (backfilled on next run)          │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  3. Single upsert back to signals document                      │     │
│  │     conviction_state, conviction_confidence,                    │     │
│  │     embedding, embedding_model                                  │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

### How

- **Idempotent**: `WHERE NOT IS_DEFINED(c.conviction_state)` means already-
  classified signals are never re-processed.
- **Soft-fail on embedding**: conviction state is always written, even if the
  embedding API call fails. A backfill loop on the next cron picks up
  null-embedding signals. The detector (Phase 5) skips null-embedding signals.
- **Prompt versioning**: the conviction prompt is stored in Key Vault as
  `conviction-prompt-v1`. Changing it without updating the version key would
  silently mix classification epochs; always bump the KV secret name.

### Why these choices

| Decision | Rationale |
|---|---|
| Combine conviction + embedding in one job | One less Container Apps Job, one fewer KV secret fetch per signal. Both operate on the same signal text. |
| Soft-fail embedding | Conviction state is the higher-value field; blocking it on an embedding timeout would stall the entire pipeline. Embeddings are recoverable; a missed conviction label is not. |
| Prompt in Key Vault | Allows prompt iteration without redeploy. Prompt changes affect classification quality systemically — the KV audit log provides traceability. |
| 200 signals/run cap | At 2 OpenAI calls per signal against 50 RPM, 25 signals/min = 8 min for 200. Fits comfortably in a 30-min slot with headroom for cold start and retries. |

---

## 7. Phase 5 — Narrative detection

### What

`job-narrative-detector` is an **hourly cron** Container Apps Job. For each
ticker with embedded signals in the last 72 hours, it runs HDBSCAN clustering
on the embedding vectors to discover topic threads, then applies the §4 lifecycle
rules to assign `lifecycle_stage` and `stage_confidence` to today's
`ticker_timeline` bucket.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  job-narrative-detector (hourly cron)                                    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  1. Fetch tickers with embeddings in last 72h                   │     │
│  │     SELECT DISTINCT VALUE c.ticker FROM c                       │     │
│  │     WHERE IS_DEFINED(c.embedding)                               │     │
│  │       AND c.createdUtc >= now - 72h                             │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          │  for each ticker:                            │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  2. HDBSCAN clustering                                          │     │
│  │     metric=cosine, min_cluster_size=3                           │     │
│  │     → cluster labels (-1 = noise)                               │     │
│  │     → dominant_cluster_fraction = largest cluster / all signals  │     │
│  │                                                                  │     │
│  │  3. Cluster merging                                              │     │
│  │     cosine(centroid_i, centroid_j) > 0.82 → merge               │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  4. assign_stage() — pure function                              │     │
│  │     inputs: tier1_pct, financial_term_density, dd_post_ratio,   │     │
│  │             gini_14d, contributor_count_growth_7d,              │     │
│  │             conviction_emotional_bull_ratio                      │     │
│  │     rules evaluated in order 1→2→3→5→6 (last match wins):      │     │
│  │       stage 1: tier1_pct < 0.20 AND ftd ≥ 0.15                │     │
│  │       stage 2: tier1_pct ∈ [0.20,0.50] AND dd ≥ 0.10          │     │
│  │                AND gini < 0.45                                  │     │
│  │       stage 3: contributor_count_growth_7d ≥ 0.30             │     │
│  │       stage 5: emotional_bull ≥ 0.50 AND gini < 0.30          │     │
│  │       stage 6: emotional_bull ≥ 0.65 AND gini ≥ 0.55          │     │
│  │     catch-all: stage 1 with confidence × 0.4                   │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  5. Upsert ticker_timeline                                      │     │
│  │     lifecycle_stage, stage_confidence,                          │     │
│  │     dominant_cluster_fraction                                   │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

### Why these choices

| Decision | Rationale |
|---|---|
| HDBSCAN over k-means | Number of topic threads per ticker is unknown. HDBSCAN discovers cluster count dynamically and labels noise as -1 (not forced into a cluster). |
| 72h embedding window | Short enough to be responsive to narrative shifts; long enough to have cluster mass. A 24h window produces too few signals per ticker for reliable clustering on most tickers. |
| Cosine merge threshold 0.82 | Empirically tuned on nuclear energy 2023–2024 and AI infrastructure 2023 test narratives. Below 0.82, distinct sub-narratives merge (noise); above, closely related threads stay split (fragmentation). |
| Override priority: last match wins | Saturation (stage 6) is the most conservative "avoid" label. Any ticker that satisfies both an early-stage rule and stage 6 should be labelled saturation — the safe outcome. |

---

## 8. Phase 6 — ACS scoring

### What

`job-acs-scorer` is a **20-minute cron** Container Apps Job. It reads today's
`ticker_timeline` documents (which now have attention metrics from the aggregator,
conviction ratios from the classifier, and lifecycle stage from the detector),
computes ACS components A–D (E=0, deferred), applies haircuts, generates CI
bands via bootstrap, and writes the score back to the same document.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  job-acs-scorer (20-min cron)                                            │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  1. Read all ticker_timeline docs for today's bucket            │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          │  for each doc:                               │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  2. Load component max weights from KV secret acs-component-    │     │
│  │     weights (falls back to design defaults: A=25,B=20,C=20,     │     │
│  │     D=20,E=15)                                                  │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  3. compute_acs() — pure functions                              │     │
│  │                                                                  │     │
│  │  A = min(dwd_14d, 1) × A_max                                    │     │
│  │  B = min(authors/log(mentions) × (1-G) × B_max, B_max)         │     │
│  │      0 when mentions ≤ 1                                        │     │
│  │  C = stage_map[stage] / 20 × stage_confidence × C_max          │     │
│  │  D = max(0, min(0.6·r_rb + 0.2·r_rB + 0.2·conv_norm, 1)) × D_max │  │
│  │  E = 0  (deferred to Phase 6.1)                                 │     │
│  │                                                                  │     │
│  │  ACS_raw = A + B + C + D                                        │     │
│  │                                                                  │     │
│  │  Haircuts (in order):                                           │     │
│  │    G > 0.65                   → × 0.6   (gini_high)            │     │
│  │    3d consecutive decel       → × 0.8   (decelerating_3d)      │     │
│  │    lifecycle_stage > 3        → × 0.5   (late_stage)           │     │
│  │    0 < market_cap < $100M     → × 0.85  (small_cap)            │     │
│  │                                                                  │     │
│  │  CI bands:                                                       │     │
│  │    ≥5 daily_buckets → bootstrap n=500, seed=hash(ticker)        │     │
│  │    < 5 daily_buckets → ±15% heuristic                           │     │
│  └───────────────────────┬─────────────────────────────────────────┘     │
│                          ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  4. Upsert back to ticker_timeline (same document)              │     │
│  │     acs, acs_ci_lower, acs_ci_upper,                            │     │
│  │     decay_acs (= acs × e^{-0.07 × days_since_scored}),         │     │
│  │     acs_components {a, b, c, d, e},                             │     │
│  │     acs_flags {gini_high, decelerating_3d, late_stage, small_cap}│    │
│  │     acs_scored_at                                               │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

### Why these choices

| Decision | Rationale |
|---|---|
| Weights in Key Vault | Calibration iteration (from the OLS backtest) should not require a redeploy. The backtest pipeline emits fitted weights; if any deviate > 20% from design, the team reviews and updates the KV secret. |
| Bootstrap CI (not parametric) | The mention-count distribution is non-Gaussian and small-sample. Bootstrap percentiles are assumption-free and naturally asymmetric. |
| Decay applied at read time (`decay_acs`) | Staleness penalty is stored as a pre-computed field rather than computed by the FastAPI backend on every request. The backend is kept strictly read-only. |
| 20-min cron (not 15-min) | Phase 6.0.1 safety buffer: gives the aggregator (15-min) and classifier (30-min, but rolling) time to complete before the scorer reads. A 15-min scorer on a 15-min aggregator had race conditions on slow Cosmos query paths. |

---

## 9. Read path — FastAPI + frontend

### What

The FastAPI backend exposes read-only routes under `/api/narrative/*`. All reads
go directly to Cosmos `ticker_timeline` — no Redis, no materialised view
(per ADR-0019). The React frontend renders the Narrative tab (gated by
`VITE_NARRATIVE_ENABLED`).

```
┌────────────────────────────────────────────────────────────────────┐
│  FastAPI  /api/narrative/*                                         │
│                                                                    │
│  GET /tickers/top          → top-N by acs DESC                    │
│  GET /tickers/{ticker}/acs → latest ACS + CI + components + flags │
│  GET /emerging             → lifecycle_stage ∈ {1,2,3},           │
│                              acs rising (decay_acs trend)          │
│  GET /narratives/{nid}     → cluster detail                       │
│  GET /alerts               → acs threshold crossings              │
│                                                                    │
│  All routes: read-only, no write, no Redis                        │
│  Cosmos read: ticker_timeline WHERE bucket_date = today()         │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  React Narrative tab (VITE_NARRATIVE_ENABLED=true)                 │
│                                                                    │
│  useNarrative.ts hook → fetch /api/narrative/tickers/top          │
│  NarrativeTickerList  → ranked table with ACS, stage badge, CI bar│
│  TickerDetailPanel    → ACS breakdown: A/B/C/D bars, flags,       │
│                         lifecycle stage explanation, timeline      │
└────────────────────────────────────────────────────────────────────┘
```

### Why no Redis (Phase 6 decision)

Redis Basic C0 was in the original Phase 6 plan. ADR-0019 removed it because:
- Cosmos Serverless read latency is < 10ms for point reads by partition key
  (ticker). The p99 < 200ms target is achievable without a cache.
- Redis Basic C0 adds ~$15/mo with no clear benefit at the current query volume.
- Cache invalidation (every 20-min scorer run) adds complexity for no gain.

Redis remains in the plan for Phase 7+ if query volume grows.

---

## 10. Azure infrastructure map

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Resource group: options-rg                                             │
│                                                                         │
│  Container Apps Environment: cae-narrative-tinkerhub                   │
│  ┌──────────────────┬────────────────────────────────────────────────┐  │
│  │ Always-on Apps   │ job-ingestor  (MinR=1, MaxR=2)                 │  │
│  ├──────────────────┼────────────────────────────────────────────────┤  │
│  │ Scheduled Jobs   │ job-extractor        5-min cron, timeout 300s  │  │
│  │ (scale-to-zero)  │ job-aggregator       15-min cron               │  │
│  │                  │ job-classifier       30-min cron               │  │
│  │                  │ job-narrative-detector  hourly cron            │  │
│  │                  │ job-acs-scorer       20-min cron               │  │
│  └──────────────────┴────────────────────────────────────────────────┘  │
│                                                                         │
│  Event Hubs Basic: evhns-narrative-tinkerhub                           │
│    Hub: reddit-raw-events  (1-day retention, $Default consumer group)  │
│                                                                         │
│  Cosmos DB Serverless: cosmos-nr-tinkerhub                             │
│    DB: narrative                                                        │
│    Container: signals          partition /ticker                        │
│    Container: ticker_timeline  partition /ticker                        │
│                                                                         │
│  Blob Storage: stnarrative<suffix>                                      │
│    Container: reddit-raw  (audit trail + EH replay source)             │
│                                                                         │
│  Key Vault: kv-narrative-tinkerhub                                     │
│    Secrets: openai-api-key, openai-endpoint, openai-deployment,        │
│             reddit-author-salt, conviction-prompt-v1,                  │
│             embed-deployment, acs-component-weights                    │
│                                                                         │
│  Azure OpenAI: gpt-4o-mini (50K TPM / 50 RPM)                         │
│               text-embedding-ada-002 (1536-dim)                        │
│                                                                         │
│  App Insights: (Basic, 5 GB cap) — system logs for all workers        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  Options Screener (separate deployment)                                 │
│                                                                         │
│  Azure Web App: backend FastAPI (incl. /api/narrative/* routes)        │
│  Azure Static Web Apps: React frontend                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 11. Inter-component contracts

This table defines what each component **produces** and **consumes**. A component
must never read from a container it does not own, and must never write to a
container it does not own.

| Component | Reads from | Writes to | Key fields produced |
|---|---|---|---|
| `job-ingestor` | Arctic Shift API | EH `reddit-raw-events`, Blob `reddit-raw/` | `post_id`, `body`, `authorHash`, `createdUtc`, `subreddit`, `flair` |
| `job-extractor` | EH `reddit-raw-events`, `signals` (dedup check) | `signals` | `ticker`, `sentiment`, `confidence`, `rationale`, `postId`, `extractedAt` |
| `job-aggregator` | `signals` | `ticker_timeline` | `dwd_14d`, `gini_14d`, `acceleration_7d`, `dd_post_ratio`, `financial_term_density`, `conviction_*_ratio`, `conviction_dd_norm`, `daily_buckets`, `attention_quality` |
| `job-classifier` | `signals` (unclassified) | `signals` | `conviction_state`, `conviction_confidence`, `embedding`, `embedding_model` |
| `job-narrative-detector` | `signals` (embeddings), `ticker_timeline` (attention fields) | `ticker_timeline` | `lifecycle_stage`, `stage_confidence`, `dominant_cluster_fraction` |
| `job-acs-scorer` | `ticker_timeline`, Key Vault `acs-component-weights` | `ticker_timeline` | `acs`, `acs_ci_lower`, `acs_ci_upper`, `decay_acs`, `acs_components`, `acs_flags`, `acs_scored_at` |
| FastAPI `/api/narrative/*` | `ticker_timeline` | — | (read-only) |

**Scheduling dependency** (not enforced by code — purely temporal):

```
job-ingestor (continuous)
  → job-extractor (every 5 min)
    → job-aggregator (every 15 min)   ← reads signals written by extractor
    → job-classifier (every 30 min)   ← reads signals written by extractor
      → job-narrative-detector (hourly) ← reads embeddings from classifier
        → job-acs-scorer (every 20 min) ← reads all ticker_timeline fields
```

The aggregator and classifier are independent of each other — they both read
from `signals` and write to different fields on `ticker_timeline` and `signals`
respectively. The scorer reads the combined output of all three.

---

## 12. Failure and recovery

| Failure | Detection | Recovery |
|---|---|---|
| `job-ingestor` crashes | Container Apps auto-restart (MinReplicas=1). App Insights alert on gap in EH publish rate. | Automatic. Blob is durable; if EH was missed, replay with `EXTRACTOR_REPLAY_FROM_START=true` on next extractor run. |
| `job-extractor` DeadlineExceeded | System log `DeadlineExceeded` event in Log Analytics. | Increase `replicaTimeout` or reduce `MAX_EVENTS_PER_RUN`. Current timeout is 300s at 40 events/run. |
| OpenAI 429 rate limit | Extractor retries on `RateLimitError` with 15–60s backoff (3 attempts). | Reduce `MAX_EVENTS_PER_RUN` or increase throttle interval. |
| EH consumer lag > 2000 | App Insights alert `event_hub_consumer_lag > 2000`. | Manual scale-up of extractor Job concurrency via Azure portal. |
| Classifier embedding soft-fail | `signals.embedding = null`. Detector skips null-embedding signals. | Auto-recovery: next classifier run picks up null-embedding signals via `WHERE NOT IS_DEFINED(c.embedding)` (separate backfill query). |
| Scorer reads stale ticker_timeline | `acs_staleness_seconds > 900` App Insights alert. | Check aggregator and classifier runs completed successfully; re-trigger scorer via `workflow_dispatch`. |
| Azure OpenAI deployment not found | 404 on extractor/classifier startup. | `az cognitiveservices account deployment list` to verify deployment name; update `openai-deployment` KV secret. |
| Budget overrun | Azure Budget alerts at $75 / $120 / $150. | Lever cascade per [ADR-0014](adr/0014-narrative-cost-substitutions.md#cost-cut-lever-cascade). |

---

## 13. Cosmos DB schema reference

Both containers use **Cosmos DB Serverless** (database `narrative`).
Documents are JSON; there is no enforced schema — the tables below document
what the platform writes and what each field means.

### `signals` container  (partition key: `/ticker`)

One document per `(post_id, ticker)` pair. The extractor creates it; the
classifier enriches it in-place.

#### Identity and routing

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `id` | str | Extractor | `"{post_id}_{ticker}"` — Cosmos document ID and natural dedup key. A post mentioning three tickers produces three documents, each with a distinct `id`. |
| `ticker` | str | Extractor | Uppercase ticker symbol (e.g. `"NVDA"`). Also the partition key — all documents for the same ticker land in the same logical partition, making per-ticker queries cheap. |
| `postId` | str | Extractor | Raw Reddit post or comment ID (e.g. `"t3_abc123"`). Shared across all ticker documents for the same post. Used by the pre-flight dedup query in the extractor. |
| `source` | str | Extractor | Origin of the post: `"reddit"` for live ingestion, `"seed_script"` for fixture data. Lets analytics filter out seeded test data. |

#### Extraction output (Phase 2)

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `sentiment` | str | Extractor | GPT-4o-mini's top-level read: `"bullish"`, `"bearish"`, or `"neutral"`. Coarser than `conviction_state` — used for quick ratio rollups in the aggregator before the classifier has run. |
| `confidence` | float `[0,1]` | Extractor | How confident GPT-4o-mini is in its own extraction (self-reported). Values below 0.5 are treated as low-signal by the aggregator's `avg_confidence` rollup. |
| `rationale` | str | Extractor | One-sentence justification extracted from the post body (e.g. `"Strong Q1 data-center beat; FCF guide raised"`). Used as the embedding input for the classifier, and displayed in the UI detail panel. |
| `extractedAt` | str (ISO 8601) | Extractor | UTC timestamp when the OpenAI extraction call completed. Separate from `createdUtc` so latency between posting and extraction can be monitored. |

#### Post metadata (Phase 1 — passed through from ingestor)

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `subreddit` | str | Extractor | Subreddit name without the `r/` prefix (e.g. `"stocks"`). Used by the aggregator to compute `tier1_pct / tier2_pct / tier3_pct` tier fractions. |
| `flair` | str \| null | Extractor | Post flair assigned by the author or moderators (e.g. `"DD"`, `"News"`). `null` when absent. Used by the aggregator to detect due-diligence posts for `dd_post_ratio`. |
| `authorHash` | str | Ingestor | `SHA-256(reddit_username + KV_salt)`, truncated to 16 hex chars. Raw usernames are never stored. The hash is stable across poll cycles so the aggregator can count `unique_authors_14d` correctly. |
| `createdUtc` | int | Extractor | Unix timestamp when the Reddit post was created. All time-window queries (`createdUtc >= cutoff`) use this field. Indexed by Cosmos range index. |

#### Conviction and embedding (Phase 4 — added by classifier)

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `conviction_state` | str \| absent | Classifier | One of 10 fine-grained states (see table below). `absent` on documents not yet classified — the classifier uses `WHERE NOT IS_DEFINED(c.conviction_state)` to find unclassified documents. |
| `conviction_confidence` | float \| absent | Classifier | Classifier's self-reported confidence in the assigned state. Values ≥ 0.80 are considered reliable; the aggregator weights them equally regardless (no threshold cut-off). |
| `embedding` | float[1536] \| null | Classifier | `text-embedding-ada-002` vector of the `rationale` text. `null` on soft-fail (embedding API error); backfilled on the next classifier run. The narrative detector skips documents where this is `null`. |
| `embedding_model` | str \| absent | Classifier | Model name that produced the embedding (e.g. `"text-embedding-ada-002"`). Stored so future embedding model migrations can filter by generation. |

**The 10 conviction states:**

| State | §3 weight | What it means |
|---|---|---|
| `researched_bull` | 1.0 | Bullish view backed by quantitative or fundamental analysis |
| `researched_bear` | 1.0 | Bearish view backed by quantitative or fundamental analysis |
| `emotional_bull` | 0.4 | Bullish but driven by hype, FOMO, or crowd momentum |
| `emotional_bear` | 0.4 | Bearish but driven by panic or reflexive negativity |
| `uncertainty` | 0.0 | Poster is undecided or explicitly sitting out |
| `earnings_focused` | 0.8 | Thesis anchored to a specific earnings event |
| `product_thesis` | 0.8 | Thesis anchored to a product launch or pipeline |
| `ecosystem_thesis` | 0.8 | Thesis about the broader sector or supply chain |
| `institutional_watch` | 0.9 | Reports institutional activity: filings, block trades, option flow |
| `exit_signal` | −0.5 | Poster reports closing or reducing a position |

---

### `ticker_timeline` container  (partition key: `/ticker`)

One document per `(ticker, bucket_date)` pair. The aggregator creates it; the
detector and scorer enrich it in-place. Today's document is overwritten on
every cron run; prior-day documents accumulate as history.

#### Identity

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `id` | str | Aggregator | `"{ticker}_{bucket_date}"` (e.g. `"NVDA_2026-05-15"`). One row per ticker per day. Upserted on every aggregator run, so it is always the freshest snapshot of today's metrics. |
| `ticker` | str | Aggregator | Uppercase ticker symbol. Partition key — enables cheap single-ticker queries by the FastAPI read path. |
| `bucket_date` | str (ISO date) | Aggregator | Calendar date of this snapshot (UTC). The scorer's `decay_acs` uses `days_since_scored = today - bucket_date` for temporal decay. |
| `computed_at` | str (ISO 8601) | Aggregator | UTC timestamp of the most recent aggregator run that wrote this document. Useful for debugging stale data. |

#### Volume

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `mentions_7d` | int | Aggregator | Count of signals with `createdUtc` in the last 7 days. Raw volume — not decay-weighted. |
| `mentions_14d` | int | Aggregator | Same for 14 days. The primary volume denominator used for ratio fields (e.g. `bullish_ratio`). |
| `mentions_30d` | int | Aggregator | Same for 30 days. Used as the baseline in the acceleration formula. |

#### §2.1 Persistence — decay-weighted density

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `decay_weighted_density_7d` | float `[0,1]` | Aggregator | Exponentially decay-weighted signal density over the last 7 days (λ=0.1, recency-biased). A 7d DWD close to the 30d DWD means interest has been steady. |
| `decay_weighted_density_14d` | float `[0,1]` | Aggregator | 14-day window. **Primary persistence input for ACS component A.** Higher = more sustained attention. |
| `decay_weighted_density_30d` | float `[0,1]` | Aggregator | 30-day window. Serves as the acceleration baseline: a high 30d DWD with low 7d DWD signals fading interest. |
| `daily_buckets` | list[object] | Aggregator | Array of `{day, count, unique_authors}` objects, one per calendar day in the last 30 days (sorted ASC). Used by the scorer's bootstrap CI — requires ≥5 buckets for statistical CI; otherwise falls back to ±15% heuristic. |

#### §2.2 Acceleration

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `acceleration_7d` | float `[−1, +∞]` | Aggregator | `(dwd_7d − dwd_30d) / dwd_30d`. Positive = attention is picking up relative to the 30d baseline. Negative = fading. The scorer applies a `decelerating_3d` haircut if this is negative for 3 consecutive daily buckets. |

#### §2.3 Contributor diversity

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `unique_authors_14d` | int | Aggregator | Count of distinct `authorHash` values in the 14d window. Used in ACS component B: `B ∝ unique_authors / log(mentions)`. More authors relative to mentions = more organic. |
| `gini_14d` | float `[0,1]` | Aggregator | Gini coefficient of per-author signal counts in the 14d window. 0 = perfectly even (every author posts once); 1 = one author posts everything. Values above 0.65 trigger the `gini_high` haircut (ACS × 0.6). |
| `contributor_count_growth_7d` | float | Aggregator | Week-over-week growth rate of unique authors. ≥0.30 fires stage-3 detection in the narrative detector (rapid community expansion). |

#### §2.4 Discussion depth

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `avg_body_len` | float | Aggregator | Average character length of signal bodies in the 14d window. Displayed in the UI; not scored directly (correlates with `financial_term_density`). |
| `dd_post_ratio` | float `[0,1]` | Aggregator | Fraction of signals that have flair `"DD"` or body keywords matching due-diligence terms. Stage-2 detection fires when this ≥ 0.10. |
| `financial_term_density` | float `[0,1]` | Aggregator | Average fraction of tokens in each signal body that are financial terms (P/E, EPS, FCF, EBITDA, etc.). Stage-1 detection fires when this ≥ 0.15. |

#### §2.5 Composite quality

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `attention_quality` | float `[0,1]` | Aggregator | Weighted composite: `0.35×persistence + 0.25×diversity + 0.25×depth + 0.15×acceleration`. Single number summarising signal quality — used as context in the UI, not directly in ACS. |

#### Sentiment rollup

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `bullish_ratio` | float `[0,1]` | Aggregator | Fraction of 14d signals where GPT extraction `sentiment = "bullish"`. Coarse signal — used as a fallback before conviction classification completes. |
| `bearish_ratio` | float `[0,1]` | Aggregator | Same for `"bearish"`. |
| `avg_confidence` | float `[0,1]` | Aggregator | Mean extraction `confidence` across 14d signals. Low values (< 0.5) indicate the model was uncertain — a proxy for ambiguous or short posts. |

#### Subreddit tier fractions

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `tier1_pct` | float `[0,1]` | Aggregator | Fraction of 14d signals from tier-1 subreddits (investing, stocks, SecurityAnalysis, ValueInvesting, Bogleheads). Higher = more fundamentally-oriented discussion. Used in stage-1 and stage-2 detection thresholds. |
| `tier2_pct` | float `[0,1]` | Aggregator | Fraction from tier-2 subreddits (wallstreetbets, options, pennystocks, …). Higher = more retail/emotional crowd. Feeds `conviction_emotional_bull_ratio` stage rules. |
| `tier3_pct` | float `[0,1]` | Aggregator | Fraction from tier-3 sector-specific subreddits (artificial, SemiConductors, biotech, …). Higher = niche enthusiast discussion rather than broad retail. |

#### Conviction ratios (aggregated from Phase 4 classifier output)

These are `null` until the classifier has processed at least one signal for this ticker.

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `conviction_researched_bull_ratio` | float \| null | Aggregator | Fraction of classified signals (14d) with `conviction_state = "researched_bull"`. ACS component D weights this at 0.60. |
| `conviction_researched_bear_ratio` | float \| null | Aggregator | Same for `"researched_bear"`. ACS component D weights this at 0.20 (negative direction). |
| `conviction_emotional_bull_ratio` | float \| null | Aggregator | Same for `"emotional_bull"`. Stage-5 and stage-6 detection use this field directly. |
| `conviction_dd_norm` | float \| null | Aggregator | Weighted mean of conviction state weights (§3 column) across all classified signals in the 14d window. Range `[−0.5, 1.0]`. ACS component D weights this at 0.20. `null` until classified. |
| `conviction_classified_14d` | int \| null | Aggregator | Count of signals in the 14d window that have been classified. The denominator for the ratios above. |

#### Phase 5 — Lifecycle stage (added by narrative detector)

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `lifecycle_stage` | int `1–6` | Detector | Stage of the narrative lifecycle as assigned by `assign_stage()`. 1 = early organic, 2 = fundamental validation, 3 = rapid expansion, 5 = saturation approaching, 6 = saturated/fading. Stage 4 is reserved. The ACS `late_stage` haircut fires when this > 3. |
| `stage_confidence` | float `[0,1]` | Detector | Confidence in the stage assignment from HDBSCAN cluster analysis. Multiplied into ACS component C. Low confidence → lower C score. |
| `dominant_cluster_fraction` | float `[0,1]` | Detector | Fraction of signals in the 72h embedding window that belong to the largest narrative cluster. High values indicate discourse is converging on a single thesis. |

#### Phase 6 — ACS score (added by scorer)

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `acs` | float `[0,75]` | Scorer | Final Attention Conviction Score after haircuts. Sum of components A + B + C + D (E = 0 in Phase 6.0). The primary ranking field for the Narrative tab. |
| `acs_ci_lower` | float `[0,75]` | Scorer | Lower bound of the 95% confidence interval (2.5th bootstrap percentile, or `acs × 0.85` heuristic when fewer than 5 daily buckets). |
| `acs_ci_upper` | float `[0,75]` | Scorer | Upper bound of the CI (97.5th percentile, or `acs × 1.15` heuristic). Wide CI = high uncertainty — displayed as an error bar in the UI. |
| `decay_acs` | float `[0,75]` | Scorer | `acs × e^{-0.07 × days_since_scored}`. Temporal penalty so old snapshots rank lower than fresh ones. Pre-computed so the read path never does date arithmetic. |
| `acs_components` | object | Scorer | Breakdown: `{a, b, c, d, e}` (each rounded to 4 decimal places). Shown in the UI detail panel so contributors can see which dimension is driving or suppressing the score. |
| `acs_flags` | list[str] | Scorer | Active haircut flags: `"gini_high"` (×0.6), `"decelerating_3d"` (×0.8), `"late_stage"` (×0.5), `"small_cap"` (×0.85). Empty list = no haircuts applied. Displayed in the UI as warning badges. |
| `acs_scored_at` | str (ISO 8601) | Scorer | UTC timestamp of the last scorer run that wrote this document. The `acs_staleness_seconds` App Insights alert fires when `now − acs_scored_at > 900s`. |

---

## Change log

- **2026-05-15** — Added §13 Cosmos DB schema reference (all fields, all writers, rationale).
- **2026-05-15** — Document created. Covers Phase 1–6 architecture as deployed.
  Component E (market confirmation) is deferred to Phase 6.1 per
  [ADR-0019](adr/0019-narrative-phase6-scorer.md).
