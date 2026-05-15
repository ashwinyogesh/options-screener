# Narrative Intelligence — Methodology

## What and why

This document specifies the scoring math, lifecycle definitions, and failure-mode
mitigations for the Reddit Narrative Intelligence platform. It is the **single
authoritative reference** for scoring constants. If the code disagrees with this
doc, the code wins **and this doc must be updated in the same PR** — same lockstep
rule that governs `SCORING_REFERENCE.md`.

The platform is **not a trading signal generator**. It is an opportunity-discovery
layer that surfaces companies where attention and conviction are forming on Reddit
*before* institutional consensus, so a human can decide whether to do the
fundamental work.

Companion architectural records:

- [ADR-0013 — Platform decision](adr/0013-narrative-intelligence-platform.md)
- [ADR-0014 — Cost substitutions for the $150/mo budget](adr/0014-narrative-cost-substitutions.md)
- [ADR-0015 — Extractor architecture simplification (no ticker-events hub, no raw-posts container)](adr/0015-extractor-architecture-simplification.md)
- [ADR-0016 — Extractor runtime defaults: body-only gate, receive window, @latest position](adr/0016-extractor-runtime-defaults.md)

## Sections

1. [Core thesis](#1-core-thesis)
2. [Attention model](#2-attention-model)
3. [Conviction states](#3-conviction-states)
4. [Narrative lifecycle](#4-narrative-lifecycle)
5. [Attention Conviction Score (ACS)](#5-attention-conviction-score-acs)
6. [Market confirmation](#6-market-confirmation)
7. [Failure modes and mitigations](#7-failure-modes-and-mitigations)
8. [Phasing and milestones](#8-phasing-and-milestones)

---

## 1. Core thesis

Reddit functions as a distributed perception engine. Its communities act as:

- **Thesis incubators** — ideas tested before institutional awareness.
- **Conviction amplifiers** — belief reinforced through social proof.
- **Early regime detectors** — narrative shifts that precede analyst coverage.

The information transmission lifecycle for a stock thesis is roughly:

```
niche technical discussion
  → early conviction (small contributor base, deep DD threads)
  → expanding awareness (more contributors, cross-sub spread begins)
  → improving fundamentals (catalysts begin confirming the narrative)
  → institutional adoption (analyst coverage, fund flows)
  → repricing
```

**Detection target: stages 1–3 only.** Stages 4–6 are consensus or saturation; by
then the trade is priced.

Why institutional consensus lags narrative formation:

- analysts require financial confirmation before publishing
- DCF models underweight optionality and regime change
- fund mandates create structural latency between thesis and allocation

We distinguish two kinds of attention:

- **Durable narrative** — thesis-grounded, catalyst-tied, multi-community,
  expanding contributor diversity, qualitative depth.
- **Transient hype** — high velocity, low depth, concentrated accounts, no
  fundamental anchor, rapid saturation.

Mention volume alone cannot tell these apart. The four-dimension attention model
in §2 can.

---

## 2. Attention model

We do not score raw mention volume. We score *attention quality* across four
orthogonal dimensions.

### 2.1 Persistence

Sustained presence over multiple days/weeks **without an external catalyst**.
Indicates organic conviction formation, not news reaction.

- Measure: exponentially decay-weighted mention density over rolling 7/14/30-day
  windows.
- Decay: $w(t) = e^{-\lambda t}$ with $\lambda = 0.1$ (half-life ≈ 7 days).
- Quality signal: $T_7 > 0.4 \cdot T_1$ after 7 days = durable interest.

### 2.2 Acceleration

Rate of change of mention velocity. Acceleration precedes repricing; deceleration
after a spike is low-quality.

- Measure: $\Delta V / \Delta t$ where $V$ is decay-weighted daily mention count
  vs. a 30-day baseline.
- Quality signal: 3+ days of sustained acceleration > 1.5 × baseline = regime-shift
  candidate.

### 2.3 Contributor diversity

Unique non-correlated accounts. Concentrated discussion is a coordination flag.

- Measure: unique author count plus the Gini coefficient of the contribution
  distribution.
- Quality signal: $G < 0.35$ healthy; $G > 0.65$ concentration flag.

### 2.4 Discussion depth

Quality of engagement beyond simple mentions. Deep threads with thesis content
precede retail expansion.

- Measure: avg comment depth, avg comment length, DD-flagged ratio, financial-term
  density.
- Quality signal: avg depth > 3 AND financial-term density > 12% = early
  conviction stage.

### 2.5 Composite weighting

When combining the four dimensions into a single attention quality score:

$$
\text{attention\_quality} = 0.35 \cdot \text{persistence}
                         + 0.25 \cdot \text{contributor\_diversity}
                         + 0.25 \cdot \text{discussion\_depth}
                         + 0.15 \cdot \text{acceleration}
$$

Persistence is the strongest single signal because it is the hardest to fake.
Acceleration is the lowest-weighted because it is the most easily produced by
coordinated activity.

---

## 3. Conviction states

Standard polarity sentiment (positive / negative / neutral) is inadequate. A
"positive" tweet about a stock can be either a researched thesis or naked
enthusiasm, and the two have opposite predictive value.

We replace polarity with **conviction state classification**. Each post or
comment is classified into exactly one of:

| State | Weight | Description |
|---|---|---|
| `researched_bull` | 1.0 | Cites data, metrics, product evidence. |
| `researched_bear` | 1.0 | Critical thesis with evidence. Healthy debate signal. |
| `emotional_bull` | 0.4 | Enthusiasm without evidence. |
| `emotional_bear` | 0.4 | FUD without evidence. |
| `uncertainty` | 0.0 | Explicitly undecided. |
| `earnings_focused` | 0.8 | Tied to specific financial events. |
| `product_thesis` | 0.8 | Driven by product/technology belief. |
| `ecosystem_thesis` | 0.8 | Driven by industry-wide tailwind. |
| `institutional_watch` | 0.9 | Mentions analyst coverage or institutional buying. |
| `exit_signal` | −0.5 | Profit-taking, conviction loss. Penalty. |

**Key insight:** *60% `researched_bull` + 20% `researched_bear`* is a higher-quality
signal than *90% `emotional_bull`*. The former is active thesis-testing; the latter
is euphoria.

### 3.1 Trajectories

The **direction of change** in the conviction-state mix is more predictive than
the snapshot:

- **Early (target):** `uncertainty` → `researched_bull` growing → `emotional_bull`
  lagging.
- **Late (avoid):** `emotional_bull` dominant → `uncertainty` growing →
  `emotional_bear` rising.

---

## 4. Narrative lifecycle

| Stage | Name | Definition (signal-side) | Trade posture |
|---|---|---|---|
| 1 | Niche technical | `tier1_pct < 0.20` AND `financial_term_density ≥ 0.15` | Watch |
| 2 | Early conviction | `tier1_pct ∈ [0.20, 0.50]` AND `dd_post_ratio ≥ 0.10` AND `gini_14d < 0.45` | **Target** |
| 3 | Expanding awareness | `contributor_count_growth_7d ≥ 0.30` (tier2-rising proxy) | **Target** |
| 4 | Institutional attention | `external_media_citations > 0` OR `analyst_name_count > 0` (Phase 6) | Late — partial |
| 5 | Consensus | `conviction_emotional_bull_ratio ≥ 0.50` AND `gini_14d < 0.30` | Avoid |
| 6 | Saturation | `conviction_emotional_bull_ratio ≥ 0.65` AND `gini_14d ≥ 0.55` | Avoid (bagholder phase) |

`tier1_pct` and `tier2_pct` are the share of mentions in Tier 1 (`r/investing`,
`r/stocks`, `r/SecurityAnalysis`, `r/ValueInvesting`, `r/Bogleheads`) and Tier 2
(`r/wallstreetbets`, `r/options`, `r/smallstreetbets`, `r/pennystocks`,
`r/TheRaceTo10Million`, `r/swingtrading`) respectively. Tier 3 is sector-specific
(`r/artificial`, `r/SemiConductors`, `r/energy`, `r/biotech`, `r/space`,
`r/geopolitics`).

Lifecycle classification runs hourly in `job-narrative-detector` after HDBSCAN
clustering on the 72h embedding window per ticker.

---

## 5. Attention Conviction Score (ACS)

ACS is a 0–100 score combining attention quality (§2), conviction quality (§3),
narrative lifecycle (§4), and market confirmation (§6) for a single ticker.

### 5.1 Components

| Symbol | Component | Max | Formula |
|---|---|---|---|
| A | Attention persistence index | 25 | $\text{decay\_weighted\_density}_{14d} \cdot 25$ (normalized to $[0,1]$ first) |
| B | Contributor quality | 20 | $\dfrac{\text{unique\_authors}_{14d}}{\log(\text{mentions}_{14d})} \cdot (1 - G) \cdot 20$ |
| C | Narrative strength | 20 | $\text{stage\_map}[\text{stage}] \cdot \text{stage\_confidence}$ |
| D | Thesis quality | 20 | $(0.6 \cdot r_{\text{rb}} + 0.2 \cdot r_{\text{rB}} + 0.2 \cdot \text{dd\_norm}) \cdot 20$ |
| E | Market confirmation | 15 | $6 \cdot \text{RS}_{14d} + 5 \cdot \text{opt\_ratio} + 4 \cdot \text{13F\_change}$ |

Where:

- $G$ is the Gini coefficient over contributor mentions in the 14-day window.
- $r_{\text{rb}}$ and $r_{\text{rB}}$ are the ratios of `researched_bull` and
  `researched_bear` posts to total classified posts.
- `dd_norm` is the count of DD-flagged posts normalized to $[0,1]$ across the
  current universe.
- `stage_map` is `{1: 10, 2: 18, 3: 20, 4: 10, 5: 5, 6: 2}`. Stages 2 and 3 are
  the target window.
- $\text{RS}_{14d}$ is sector-relative strength over 14 days from yfinance.
- `opt_ratio` is options volume / open interest from yfinance options chain.
- `13F_change` is the most recent quarterly change from SEC EDGAR 13F filings.

### 5.2 Composite

$$
\text{ACS}_{\text{raw}} = A + B + C + D + E
$$

### 5.3 Adjustments

Multiplicative haircuts, applied in order. Multipliers floor strictly above zero
so a punished score remains debuggable.

| Condition | Multiplier | Rationale |
|---|---|---|
| $G > 0.65$ | $\times 0.6$ | Concentration / coordination risk |
| Acceleration negative for 3 days | $\times 0.8$ | Decay penalty |
| Lifecycle stage > 3 | $\times 0.5$ | Lateness penalty |
| Market cap < $100M | $\times 0.85$ | Liquidity discount |

### 5.4 Time decay

When no new signal arrives, ACS decays exponentially:

$$
\text{ACS}(t) = \text{ACS}_0 \cdot e^{-0.07 t}
$$

with $t$ in days. Half-life ≈ 10 days.

### 5.5 Runtime configuration

Weights are read at Job startup from Key Vault secret `acs-component-weights`.
**Calibration changes do not require redeploy.** The Phase 6 backtest pipeline
emits an OLS-fitted set of weights per component; if any deviates by more than
**20%** from the design weight in this document, the report flags it for manual
review and (after sign-off) the Key Vault secret is updated.

### 5.6 Confidence interval

The scorer emits `acs_ci_lower` and `acs_ci_upper` per ticker, derived from
bootstrap resampling of the 14-day post window. Used to suppress alerts where
the CI straddles the alert threshold.

---

## 6. Market confirmation

Narrative formation precedes financial confirmation because:

- financial statements reflect the past; narratives model the future
- analyst models anchor to consensus; regime change breaks the consensus prior
- institutional mandates require financial confirmation before allocation

Early confirmation signals, ordered by temporal precedence:

1. **Relative strength emergence** — outperforming sector on below-average volume.
2. **Options positioning** — call skew building before catalyst dates.
3. **Short interest decline** — slow covering by informed shorts (not a squeeze).
4. **Revenue acceleration** — organic growth rate increasing, often in one segment.
5. **Management language shift** — guidance moves from defensive to expansive.
6. **Insider buying** — open-market purchases (not option exercises).

ACS Component E captures (1)–(3) at scoring time. (4)–(6) are flagged manually in
the Narrative tab and surfaced in the per-ticker drilldown when present.

**Avoid using price action as the primary signal.** A stock that is up 30% has
already confirmed, and is probably no longer in stages 1–3.

---

## 7. Failure modes and mitigations

| # | Mode | Signals | Mitigation |
|---|---|---|---|
| 1 | Coordinated manipulation | Sudden volume spike, $G > 0.65$, identical phrasing | Gini penalty (0.6×); Postgres `UNIQUE (body_sha256, hour_bucket)` dedup; author-weight floor |
| 2 | Low-float distortion | High ACS from a small community, tiny float amplifies price | Market-cap < $100M discount (0.85×); float-adjusted mention norm |
| 3 | Narrative collapse | Catalyst fails, conviction evaporates | 10-day decay half-life; `catalyst_event` tagging in `ticker_timeline` |
| 4 | False technology narratives | Speculative thesis (quantum, RTSC) with no commercial path | Thesis-quality DD scoring; low financial-term density penalty; full Component D requires product/revenue anchor |
| 5 | Late-stage euphoria | High volume, all `emotional_bull`, community saturated | Stage > 3 penalty (0.5×); `emotional_bull` dominance flag; rising-Gini-3d flag |
| 6 | Event Hubs consumer lag | Extractor lags, ACS becomes stale | App Insights alert on `event_hub_consumer_lag > 2000`; manual scale-up of extractor Job concurrency; Blob is durable backup |
| 7 | Azure budget overrun | MTD spend > $130 mid-month | Lever cascade — see [ADR-0014](adr/0014-narrative-cost-substitutions.md#cost-cut-lever-cascade) |
| 8 | Reddit ToS violation | Raw usernames persisted, ML training on corpus | SHA-256 author hashing with Key Vault salt; system stays internal-only; no commercial redistribution; no ML fine-tuning on Reddit corpus |

---

## 8. Phasing and milestones

Each phase = one or more GitHub Actions workflows targeting Azure. Secrets via
Key Vault. Images via ghcr.io.

### Phase 0 — Repo integration (week 0, this PR)

- `backend/services/narrative/`, `backend/routers/narrative.py`,
  `backend/services/narrative_db.py` (stubs)
- `frontend/src/components/Narrative*.tsx`, `hooks/useNarrative.ts`,
  `types/narrative.ts`, Narrative tab registered in `App.tsx`
  (hidden by default like DCF, gated by `VITE_NARRATIVE_ENABLED`)
- `infra/main.bicep` + module skeletons under `infra/modules/`
- `workers/{ingestion,extractor,aggregator,classifier,narrative,scorer}/`
  Dockerfiles + Python project skeletons
- `.github/workflows/narrative-*.yml` per worker (manual `workflow_dispatch`
  initially; cron triggers added in their owning phase)
- Azure Budget alerts at $75 / $120 / $150 — provisioned via the Bicep
  monitoring module in Phase 1, but configured as part of Phase 0 planning
- ADR-0013, ADR-0014, this document

### Phase 1 — Foundation (weeks 1–3)

- Bicep: Event Hubs Basic, Blob, Key Vault, Container Apps env, App Insights
- Code: `ca-ingestion` (always-on, MinReplicas=1, MaxReplicas=2); Arctic Shift
  API polling (original plan was PRAW; shipped with RSS polling, then switched
  to Arctic Shift during Phase 2 — see ADR-0016); Blob-first durability,
  Event Hubs publish second
- CI/CD: GitHub Actions builds image → ghcr.io → `az containerapp update`

### Phase 2 — Extraction (weeks 4–5)

- Bicep: Cosmos DB Serverless (replaces Postgres — subscription-restricted,
  see ADR-0014); two containers: `signals` (partition `/ticker`) and
  `narratives` (partition `/ticker`, Phase 4+). `raw-posts` container removed
  — ingestion writes to Blob Storage only (see ADR-0015).
- Ingestion: switched from PRAW/RSS to Arctic Shift API
  (`arctic-shift.photon-reddit.com`) — full post body + top comments, no auth
  required, works from Azure IPs.
- Code: `job-extractor` Container Apps Job; Layer 1 cost gate: skip posts with
  body < 20 chars. Score-based filtering deferred to Phase 3 — Arctic Shift
  returns `score=1` for posts < 36h old (archival lag makes score unreliable
  in real-time). See ADR-0016.
- Receive window: `RECEIVE_WINDOW_SECONDS=25` (default); EH `starting_position`
  defaults to `@latest` (skip stale backlog). Set
  `EXTRACTOR_REPLAY_FROM_START=true` for initial catch-up replay. See ADR-0016.
- Test: precision ≥ 0.92 (high) / ≥ 0.80 (medium) on 500 hand-labeled mentions

### Phase 3 — Attention modeling (weeks 6–7)

- Code: `job-aggregator` (15-min cron) writes `ticker_timeline`; pure functions
  in `services/narrative/attention.py`
- Test: decay/Gini accuracy on synthetic fixtures with hand-computed expected
  values

### Phase 4 — Conviction classifier (weeks 8–9)

- Bicep: Azure OpenAI deployment `gpt-4o-mini` (50K TPM / 50 RPM conservative);
  region check `centralus` first, fallback `eastus2`
- Code: `job-classifier` (30-min cron) processes signals where
  `NOT IS_DEFINED(c.conviction_state)`; structured-output JSON; prompt template
  stored in Key Vault as `conviction-prompt-v1`. Embedding generation runs in
  the same job (see Phase 5); an embedding failure does **not** block the
  conviction-state write — see
  [ADR-0018](adr/0018-classifier-embedding-soft-fail.md).
- **No Azure ML.** Fine-tune escalation gated on F1 < 0.78
- Test: F1 ≥ 0.78 on `researched_bull` against a 300-post eval set

### Phase 5 — Narrative detection (weeks 10–12)

- Bicep: embeddings co-located on the `signals` Cosmos container under the
  `embedding` key (already pre-provisioned with `excludedPaths: ['/embedding/?']`
  in Phase 2 Bicep — see [ADR-0017](adr/0017-narrative-phase5-detector.md)).
  Azure OpenAI deployment `text-embedding-ada-002` (1 536-dim), overridable via
  the `embed-deployment` Key Vault secret — see
  [ADR-0018](adr/0018-classifier-embedding-soft-fail.md).
- Code: embedding generation merged with the classifier Job (one OpenAI round-trip
  per post; embedding failure is soft and recovered by a backfill loop on the next
  cron). `job-narrative-detector` (hourly) runs HDBSCAN + lifecycle assignment;
  cluster merge cosine sim threshold 0.82.
- Test: ≥7/10 known historical narratives correctly staged (e.g. nuclear energy
  2023–2024, AI infrastructure 2023)

### Phase 6 — ACS, backtest, App Service integration (weeks 13–16)

- Bicep: Redis Basic C0; App Insights workspace (Basic, 5 GB cap); refresh Azure
  Budget alerts
- Code: `job-acs-scorer` (15-min); `scripts/backtest_narrative.py` + optional
  `job-backtest`; FastAPI routes under `/api/narrative/*`; Static Web Apps
  Narrative tab
- Test:
  - Backtest IC ≥ 0.04 at T+30 on held-out 90 days
  - `GET /api/narrative/tickers/top` p99 < 200ms with Redis warm
  - App Insights: `acs_staleness_seconds < 900` for ≥99% of any 24h window
  - Frontend smoke: synthetic high-ACS ticker visible within 30 minutes

---

## Change log

- **2026-05-14** — Phase 6 closed out: `job-acs-scorer` writing ACS, CI bands,
  decay_acs, components, and flags onto `ticker_timeline` every 15 min;
  FastAPI `/api/narrative/*` routes serve directly from Cosmos (no Redis,
  per ADR-0019); `scripts/backtest_narrative.py` provides the IC@T+30
  calibration harness. Component E remains 0 (deferred to Phase 6.1).
- **2026-05-12** — Initial document created alongside ADR-0013 and ADR-0014.
