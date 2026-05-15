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

Fractional excess of recent attention over the 30-day baseline. Acceleration
precedes repricing; deceleration after a spike is low-quality.

- Measure: $\text{accel} = \dfrac{\text{dwd}_{7d} - \text{dwd}_{30d}}{\text{dwd}_{30d}}$
  where both densities are the normalized [0,1] persistence values from §2.1.
  Returns 0 when the 30d baseline is 0. Unbounded above; callers clip for display.
- Quality signal: $\text{accel} > 0.5$ ⇔ 7d density is at least 1.5× the 30d
  baseline = regime-shift candidate. This 1.5× threshold is what sets the
  acceleration saturation point (`_QUALITY_ACCEL_SAT = 0.5`) in §2.5.
- Negative acceleration is a deceleration signal and (a) contributes 0 to §2.5
  quality and (b) triggers the §5.3 `decelerating_3d` haircut when sustained
  for 3 consecutive days.

### 2.3 Contributor diversity

Unique non-correlated accounts. Concentrated discussion is a coordination flag.

- Measure: unique author count plus the Gini coefficient of the contribution
  distribution. **Both are scoped to the 14-day window** — per-author mention
  counts feeding `gini_14d` only include signals inside that window so that
  pre-window activity does not skew the diversity metric.
- Quality signal: $G < 0.35$ healthy; $G > 0.65$ concentration flag.

### 2.4 Discussion depth

Quality of engagement beyond simple mentions. Deep threads with thesis content
precede retail expansion.

- Scoring inputs (each in [0, 1]):
  - **DD-flagged post ratio** (`dd_post_ratio`) — fraction of 14d posts whose
    flair text or first 200 chars of body match a DD keyword
    (`dd`, `due diligence`, `deep dive`, `analysis`, `thesis`, `research`,
    `writeup`, `bull case`, `bear case`).
  - **Financial-term density** (`financial_term_density`) — per-post,
    $\min(\text{distinct financial terms matched} / \text{token count}, 1)$,
    averaged across the 14d window. Matching is case-insensitive substring
    against a curated vocabulary in `attention.py::_FINANCIAL_TERMS` (revenue,
    eps, ebitda, valuation, fcf, guidance, …).
- Stored-only display field: **`avg_body_len`** — mean rationale length across
  the 14d window. Surfaced in the per-ticker drilldown; not scored.
- Quality signal: `financial_term_density > 0.12` AND `dd_post_ratio > 0.10` =
  early-conviction depth profile.
- Phase 6+ deferred: avg comment depth / avg comment length would require
  ingesting full Reddit comment trees, which the Arctic Shift pipeline does not
  currently retain.

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

Each input must be in $[0, 1]$ before the weighted sum. Normalization is
implemented in `_normalize_for_quality` (`backend/services/narrative/attention.py`
and the worker mirror):

- persistence = $\text{clip}(\text{dwd}_{14d}, 0, 1)$.
- diversity   = $\min(\frac{\text{unique\_authors}_{14d}}{20}, 1) \cdot (1 - G_{14d})$.
- depth       = $0.5 \cdot \text{financial\_term\_density} + 0.5 \cdot \text{dd\_post\_ratio}$.
- acceleration = $\text{clip}(\frac{\text{acceleration}_{7d}}{0.5}, 0, 1)$ (negative
  acceleration contributes 0).

The result is written as `attention_quality` on every `ticker_timeline` document.
The ACS components (§5) continue to consume the raw inputs directly; the
composite is a dashboard / ranking signal, not an ACS input.

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

**Persisted aggregates (per 14d window).** The aggregator computes four
conviction summaries onto each `ticker_timeline` document:

- `conviction_researched_bull_ratio` — fraction of classified 14d signals.
- `conviction_researched_bear_ratio` — same.
- `conviction_emotional_bull_ratio` — same. Used by §4 lifecycle stages 5/6.
- `conviction_dd_norm` — the **weighted-conviction mean**: the average of the
  per-state weights above, taken over all classified 14d signals. Range
  $[-0.5, 1.0]$. Fed into §5 Component D as `conv_norm`.

The remaining seven states (`emotional_bear`, `uncertainty`, `earnings_focused`,
`product_thesis`, `ecosystem_thesis`, `institutional_watch`, `exit_signal`) are
not persisted as separate ratios; their effect on scoring flows entirely through
`conviction_dd_norm`.

### 3.1 Trajectories

The **direction of change** in the conviction-state mix is more predictive than
the snapshot:

- **Early (target):** `uncertainty` → `researched_bull` growing → `emotional_bull`
  lagging.
- **Late (avoid):** `emotional_bull` dominant → `uncertainty` growing →
  `emotional_bear` rising.

> **Status:** descriptive. Trajectory deltas are recoverable from the
> `ticker_timeline` history (each snapshot stores a same-day point estimate)
> but no metric, flag, or ACS adjustment currently consumes them. Phase 6.1
> candidate — see [ADR-0019](adr/0019-narrative-phase6-scorer.md).
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
| D | Thesis quality | 20 | $\max(0, \min(0.6 \cdot r_{\text{rb}} + 0.2 \cdot r_{\text{rB}} + 0.2 \cdot \text{conv\_norm},\ 1)) \cdot 20$ |
| E | Market confirmation | 15 | $6 \cdot \text{RS}_{14d} + 5 \cdot \text{opt\_ratio} + 4 \cdot \text{13F\_change}$ |

Where:

- $G$ is the Gini coefficient over contributor mentions in the 14-day window.
- $r_{\text{rb}}$ and $r_{\text{rB}}$ are the ratios of `researched_bull` and
  `researched_bear` posts to total classified posts.
- `conv_norm` is the field stored as `conviction_dd_norm` on `ticker_timeline`:
  the mean of the §3 per-state weights over classified 14d signals, range
  $[-0.5, 1.0]$. Component D is then floored at 0 so every component stays in
  $[0, \text{max}]$ — a wave of `exit_signal` posts cannot drive D negative.
  (The legacy name `dd_norm` is retained in the Cosmos field for backward
  compatibility; treat it as `conv_norm` everywhere in this doc.)
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

- **2026-05-15** — §3 / §5.1 conviction alignment pass:
  - **Code fix (§5.1 Component D)**: scorer now floors `comp_d` at 0
    (`max(0, min(thesis_score, 1)) * D_max`). An `exit_signal`-dominated 14d
    window previously could push the component negative because the third
    term `conv_norm ∈ [-0.5, 1.0]`. New regression test
    `test_floored_at_zero_for_exit_signal_dominant`. Local variable renamed
    `dd_norm` → `conv_norm` in the scorer; the Cosmos field stays
    `conviction_dd_norm` for backward compatibility.
  - **Doc fix (§3)**: documented the four persisted aggregates
    (`researched_bull` / `researched_bear` / `emotional_bull` ratios +
    `conviction_dd_norm` weighted mean). Noted explicitly that the other
    seven states feed scoring only via the weighted mean.
  - **Doc fix (§3.1)**: marked trajectories as descriptive — recoverable from
    `ticker_timeline` history but not yet consumed by any metric or
    adjustment. Phase 6.1 candidate.
  - **Doc fix (§5.1)**: corrected the third Component D term — was claimed
    to be "DD-flagged posts normalized to [0,1] across the current universe",
    is actually `conv_norm` (mean of §3 conviction weights). Formula updated
    to show the new $\max(0, \min(\cdot, 1))$ floor.
- **2026-05-14c** — Attention model §2 audit pass:
  - **Code fix (§2.3)**: `gini_14d` now uses per-author mention counts scoped
    to the 14-day window (`author_mentions_14d`). Previously it used 30-day
    totals for the 14d-active author set, which let pre-window activity skew
    diversity. Affects both `backend/services/narrative/attention.py` and
    `workers/aggregator/attention.py`. New regression test
    `test_gini_14d_ignores_pre_window_activity`.
  - **Doc tightening (§2.2)**: restated acceleration formula as fractional
    excess over 30d baseline (matches `compute_acceleration` code) and tied
    the §2.5 `_QUALITY_ACCEL_SAT = 0.5` saturation point back to the 1.5×
    baseline threshold.
  - **Doc tightening (§2.4)**: replaced the four-measure list (which included
    `avg comment depth`, never implemented) with the two metrics actually
    scored (`dd_post_ratio`, `financial_term_density`) plus `avg_body_len`
    as a display-only field. Documented the `_FINANCIAL_TERMS` substring
    matching strategy explicitly.
- **2026-05-14b** — Scorer brought into full §5 compliance: small-cap haircut
  (`market_cap < $100M × 0.85`) wired with a yfinance lookup in the scorer
  worker; the `acceleration_7d < 0` proxy replaced by a true 3-day decreasing
  mention-count streak read from `daily_buckets`; the ±15% heuristic CI
  replaced by a bootstrap (n=500, seeded off ticker) over the same
  `daily_buckets`, falling back to the heuristic when fewer than 5 days are
  available. Flag rename: `decelerating` → `decelerating_3d`; new flag
  `small_cap`. See [ADR-0019](adr/0019-narrative-phase6-scorer.md).
- **2026-05-14** — Phase 6 closed out: `job-acs-scorer` writing ACS, CI bands,
  decay_acs, components, and flags onto `ticker_timeline` every 15 min;
  FastAPI `/api/narrative/*` routes serve directly from Cosmos (no Redis,
  per ADR-0019); `scripts/backtest_narrative.py` provides the IC@T+30
  calibration harness. Component E remains 0 (deferred to Phase 6.1).
- **2026-05-12** — Initial document created alongside ADR-0013 and ADR-0014.
