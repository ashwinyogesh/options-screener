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
- [NARRATIVE_SYSTEM_DESIGN.md — End-to-end component diagram, data flow, and design rationale](NARRATIVE_SYSTEM_DESIGN.md)

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

## 3. Conviction axes

Standard polarity sentiment (positive / negative / neutral) is inadequate. A
"positive" tweet about a stock can be either a researched thesis or naked
enthusiasm, and the two have opposite predictive value.

As of [ADR-0020](adr/0020-multi-axis-conviction-schema.md) (and
[ADR-0021](adr/0021-retire-legacy-conviction-taxonomy.md) which removed the
back-compat 10-state derivation) the classifier emits a structured object
with **four independent axes** plus a confidence score:

| Axis | Values | Captures |
|---|---|---|
| `direction` | `bull` / `bear` | Net stance on the ticker. |
| `substance` | `researched` / `emotional` | Whether evidence is present. |
| `driver` | `earnings` / `product` / `macro` / `flows` / `valuation` / `other` | What the post is reacting to. |
| `position` | `entering` / `holding` / `exiting` / `unstated` | Lifecycle of the author's trade. |
| `confidence` | `[0.0, 1.0]` | Model self-rated certainty. |

The four axes are orthogonal: `(bull, researched, earnings, entering)` and
`(bull, emotional, flows, exiting)` are very different signals that a single
categorical label collapses into the same bucket. The 14-day aggregator
persists five **marginal** shares and two **joint** shares onto each
`ticker_timeline`:

Marginals (drive UI + §4 lifecycle rules):

- `conviction_bull_share` — fraction of classified signals with `direction=bull`.
- `conviction_researched_share` — fraction with `substance=researched`.
- `conviction_entering_share` — fraction with `position=entering`.
- `conviction_exiting_share` — fraction with `position=exiting`.
- `conviction_driver_top` — most-common non-`other` driver (or `"other"` on tie / all-other).

Joint shares (drive §5 Component D):

- `conviction_bull_researched_share` — `direction=bull ∧ substance=researched`.
- `conviction_bear_researched_share` — `direction=bear ∧ substance=researched`.

Joint shares are *not* derivable from the marginals (the axes are not
independent in practice), so the aggregator computes them directly from the
signal stream. All seven fields are `null` until the classifier has labelled
at least one signal in the 14d window.

### 3.1 Trajectories

The **direction of change** in the axis distributions is more predictive than
the snapshot:

- **Early (target):** rising `conviction_researched_share` with low
  `conviction_bull_share`, `conviction_entering_share` non-trivial.
- **Late (avoid):** `conviction_bull_share` high while
  `conviction_researched_share` falls; `conviction_exiting_share` creeping up.

> **Status:** descriptive. Trajectory deltas are recoverable from the
> `ticker_timeline` history but no metric, flag, or ACS adjustment currently
> consumes them. Phase 6.1 candidate — see [ADR-0019](adr/0019-narrative-phase6-scorer.md).
---

## 4. Narrative lifecycle

The detector classifies each ticker into a lifecycle stage hourly using
**smoothed inputs + monotone hysteresis** (ADR-0029). Earlier versions
applied the boolean rules below directly against the raw bucket snapshot;
that produced day-rollover cliffs and Stage 3 lock-in (the
`contributor_count_growth_7d ≥ 0.30` rule was unconditional and overrode
Stages 1/2). The current model preserves the same semantic stages but
arrives at them through a continuous score with controlled transitions.

### 4.1 Stages

| Stage | Name | Semantic definition | Trade posture |
|---|---|---|---|
| 0 | Insufficient data | HDBSCAN produced no clusters in the 72h window | Skip |
| 1 | Niche technical | Narrow contributor base, mostly substantive (low breadth score) | Watch |
| 2 | Early conviction | Some Tier-1 spread, substantive DD, low concentration | **Target** |
| 3 | Expanding awareness | Wide contributor growth, mainstream Tier-1 spread | **Target** |
| 4 | Institutional attention | `external_media_citations > 0` OR `analyst_name_count > 0` — **not yet implemented** (deferred to Phase 6.1) | Late — partial |
| 5 | Consensus | `conviction_bull_share ≥ 0.65` AND `conviction_researched_share < 0.40` AND `gini_14d < 0.30` | Avoid |
| 6 | Saturation | `conviction_bull_share ≥ 0.75` AND `conviction_researched_share < 0.30` AND `gini_14d ≥ 0.55` | Avoid (bagholder phase) |

Stages 1–3 are now driven by a **continuous breadth score** (§4.3), not
boolean rules. Stages 5–6 retain their explicit axis-share conditions
because they describe distinct narrative dimensions (consensus posture)
rather than positions on the breadth axis.

### 4.2 EMA smoothing

Each detector run EMA-smooths the volatile aggregator inputs before any
classification:

$$ s_t = \alpha \cdot x_t + (1 - \alpha) \cdot s_{t-1}, \quad \alpha = 0.4 $$

This gives an effective half-life of roughly **3 detector runs** (≈ 3 hours
with the hourly schedule). Inputs smoothed: `tier1_pct`, `tier2_pct`,
`gini_14d`, `dd_post_ratio`, `financial_term_density`,
`contributor_count_growth_7d`, `conviction_bull_share`,
`conviction_researched_share`. Cold start (no prior smoothed value) takes
the first reading as-is. The smoothed state is persisted on each timeline
bucket under `lifecycle_state.smoothed_inputs` and survives
day-bucket rollovers and aggregator gaps.

### 4.3 Breadth score (continuous)

$$ \text{breadth} = 0.5 \cdot \widetilde{\text{tier1}} + 0.3 \cdot \text{clip}\!\left(\frac{\widetilde{\text{growth}}}{0.5}, 0, 1\right) + 0.2 \cdot \widetilde{\text{dd\_post}} $$

(tildes denote EMA-smoothed values). The weights reflect the relative
contribution of each dimension to "narrative breadth": Tier-1 mainstream
spread dominates, week-over-week contributor growth indicates expansion,
and DD-post ratio adds a substance multiplier.

### 4.4 Score → stage band

| Breadth score | Target stage |
|---|---|
| `< 0.15` | 1 — Niche |
| `[0.15, 0.35)` | 2 — Early conviction |
| `≥ 0.35` | 3 — Expanding |

Stage 5/6 overlay (§4.1) replaces the breadth-band target whenever its
axis-share condition holds against the **smoothed** axis shares.

### 4.5 Monotone hysteresis

The detector never jumps more than ±1 stage per commit, and a new target
must be observed for `confirm_runs = 2` consecutive runs before the move
is committed. State carried on the bucket (`lifecycle_state.pending_stage`,
`lifecycle_state.pending_streak`) tracks the candidate move between runs.

Worked examples (assume aggregator metrics stable):

| Run | prev | target | pending → | committed | Notes |
|---|---|---|---|---|---|
| 1 (cold start) | 0 | 3 | — | **3** | Cold start accepts target immediately |
| 1 | 1 | 3 | pending=3, streak=1 | 1 | Held, awaiting confirmation |
| 2 | 1 | 3 | reset | **2** | Confirmed; +1 step cap → moves 1→2 (not 1→3) |
| 3 | 2 | 3 | pending=3, streak=1 | 2 | New observation of higher target |
| 4 | 2 | 3 | reset | **3** | Confirmed second time → 2→3 |
| · | 1 | 3 (run a), 2 (run b) | pending changes | 1 | Changing target resets `pending_streak` |

A 1 → 3 jump therefore takes **at least 4 detector runs** (≈ 4 hours).
Stage 0 (insufficient data) is short-circuited: it preserves prior state
rather than overwriting committed history with a single quiet window.

### 4.6 Confidence

$$ \text{confidence} = \text{dominant\_fraction} \cdot \text{certainty} \cdot \text{proximity} $$

- `dominant_fraction` is the share of non-noise signals in the largest
  HDBSCAN cluster (§3.1).
- `certainty = 1.0` when committed stage equals target stage; `0.5` when
  the detector is mid-transition (committed != target).
- `proximity ∈ [0, 1]` falls linearly to 0 at the band boundaries — a
  score sitting exactly on a threshold is reported with low confidence to
  signal that the next run could flip the band.

### 4.7 State persistence

Each detector run writes the following onto today's timeline bucket:

```jsonc
{
  "id": "AAPL_2025-04-13",
  "lifecycle_stage": 2,
  "stage_confidence": 0.78,
  "lifecycle_state": {
    "smoothed_inputs": { "tier1_pct": 0.34, "...": "..." },
    "pending_stage": 3,
    "pending_streak": 1
  }
}
```

State is read in this order at the start of each run: (1) today's bucket
if a previous same-day run wrote `lifecycle_state`; (2) yesterday's
bucket; (3) cold start. This keeps hysteresis hour-by-hour, not just
day-by-day.

`tier1_pct` and `tier2_pct` are the share of mentions in Tier 1 (`r/investing`,
`r/stocks`, `r/SecurityAnalysis`, `r/ValueInvesting`, `r/Bogleheads`) and Tier 2
(`r/wallstreetbets`, `r/options`, `r/smallstreetbets`, `r/pennystocks`,
`r/TheRaceTo10Million`, `r/swingtrading`) respectively. Tier 3 is sector-specific
(`r/artificial`, `r/SemiConductors`, `r/energy`, `r/biotech`, `r/space`,
`r/geopolitics`). `tier2_pct` and `tier3_pct` are persisted on `ticker_timeline`
for the drilldown UI but are not currently consumed by stage logic.

Lifecycle classification runs hourly in `job-narrative-detector` after HDBSCAN
clustering on the 72h embedding window per ticker.

---

## 5. Attention Conviction Score (ACS)

ACS is a 0–100 score combining attention quality (§2), conviction quality (§3),
narrative lifecycle (§4), and market confirmation (§6) for a single ticker.

### 5.1 Components

| Symbol | Component | Max | Formula |
|---|---|---|---|
| A | Attention persistence index | 25 | $\min(\text{decay\_weighted\_density}_{14d},\ 1) \cdot A_{\max}$ |
| B | Contributor quality | 20 | $\min\!\left(\dfrac{\text{unique\_authors}_{14d}}{\log(\text{mentions}_{14d})} \cdot (1 - G) \cdot B_{\max},\ B_{\max}\right)$; $0$ when $\text{mentions}_{14d} \le 1$ |
| C | Narrative strength | 20 | $\dfrac{\text{stage\_map}[\text{stage}]}{\max(\text{stage\_map})} \cdot \text{stage\_confidence} \cdot C_{\max}$ |
| D | Thesis quality | 20 | $\min(0.6 \cdot s_{\text{br}} + 0.2 \cdot s_{\text{Br}},\ 1) \cdot D_{\max}$ |
| E | Market confirmation | 15 | $6 \cdot \tilde{\text{RS}}_{14d} + 5 \cdot \tilde{\text{opt}} + 4 \cdot \tilde{\text{13F}}$; each sub-signal normalized to $[0, 1]$ — see normalization curves below |

Where:

- $G$ is the Gini coefficient over contributor mentions in the 14-day window.
- $s_{\text{br}}$ and $s_{\text{Br}}$ are the joint shares
  `conviction_bull_researched_share` and `conviction_bear_researched_share`
  on `ticker_timeline` — the fraction of classified 14d signals where the
  direction and substance axes co-occur. Both are bounded in $[0, 1]$ so
  Component D lives in $[0, D_{\max}]$ without further flooring. See
  [ADR-0021](adr/0021-retire-legacy-conviction-taxonomy.md) for why the
  earlier 0.2 weight on `conv_norm` was retired.
- `stage_map` is `{1: 10, 2: 18, 3: 20, 4: 10, 5: 5, 6: 2}`. Stages 2 and 3 are
  the target window. Component C divides by $\max(\text{stage\_map}) = 20$ so
  that a perfectly-staged, fully-confident narrative scores exactly $C_{\max}$
  regardless of how the KV-overridable $C_{\max}$ is recalibrated (§5.5). An
  assertion in the scorer enforces this invariant at module load.
- $\text{RS}_{14d}$ is sector-relative price strength over 14 days from yfinance.
  The raw value is the ticker's 14-day return minus its SPDR sector-ETF return
  (SPY used as fallback for unmapped sectors).
  **Normalization:** $\tilde{\text{RS}}_{14d} = \text{clip}(\text{RS}_{14d} / 0.20,\ 0.0,\ 1.0)$
  where the cap $0.20$ means 20\% outperformance saturates the signal. Negative
  excess return (underperforming sector) is floored at 0 — absent confirmation
  is neutral, not negative.
- `opt_ratio` is the total call volume divided by total call open interest across
  all strikes for the nearest available expiry from yfinance options chain.
  **Normalization:** $\tilde{\text{opt}} = \min(\text{opt\_ratio} / 2.0,\ 1.0)$
  where a ratio of 2.0 (call vol = 2× OI) saturates the signal.
- `13F_change` is the net institutional buying signal from yfinance holder data:
  $\text{net\_pct} = \sum \text{Change} / \sum \text{Shares}$ over the top
  institutional holders returned by yfinance.
  **Normalization:** $\tilde{\text{13F}} = \text{clip}(\text{net\_pct} / 0.05,\ 0.0,\ 1.0)$
  where net 5\% institutional buying saturates the signal.

All three sub-signals are fetched by `workers/scorer/market_confirmation.py`
(via `get_market_confirmation()`) and injected into the Cosmos doc dict before
`compute_acs()` is called. They are **not persisted to Cosmos** — fetched fresh
each scorer run. Any sub-signal that fails (yfinance unavailable, no options
chain, no holder data) returns 0.0; the scorer degrades gracefully.

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
| $0 < \text{market\_cap} < \$100\text{M}$ | $\times 0.85$ | Liquidity discount |

The small-cap guard requires a **strictly positive** market cap: a missing or
zero `market_cap` field (yfinance lookup failed, or ticker is a non-equity)
leaves the multiplier at 1.0 rather than punishing the score for absent data.

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

The scorer emits `acs_ci_lower` and `acs_ci_upper` per ticker. The primary
estimator is a **percentile bootstrap** (n = 500, seeded off the ticker for
reproducibility) over the 14-day `daily_buckets`: each resample recomputes
Component A from a with-replacement draw of the daily mention counts while
holding B/C/D/E and the §5.3 multiplier constant (they aggregate over the same
window or are doc-level constants), then takes the 2.5 / 97.5 percentiles of
the resulting ACS distribution.

**Fallback.** When fewer than 5 daily buckets are available (not enough samples
for a meaningful resample), the scorer emits a $\pm 15\%$ heuristic band
($\text{acs} \cdot [0.85,\ 1.15]$, clipped to $[0, 100]$).

**Defensive clamp.** The returned CI is clamped to bracket the point estimate
($\text{lower} \le \text{acs} \le \text{upper}$). In production this is a
no-op because the stored `decay_weighted_density_14d` and `daily_buckets` are
written by the same aggregator pass; it only bites in tests or partial replays
where the two can drift.

Used to suppress alerts where the CI straddles the alert threshold.

### 5.7 Continuity fields (ADR-0023)

Three scalar fields written alongside `acs` on every scorer run let the UI
distinguish multi-week durable narratives from one-day spikes without
re-fetching history at read time. See
[ADR-0023](adr/0023-emerging-continuity-fields.md) for the design.

**`stage_streak_days`** — count of consecutive days ending today where
`lifecycle_stage ∈ {1, 2, 3}` (the emerging window §4). Computed by walking
the per-ticker `ticker_timeline` history newest-first and incrementing
while the stage stays in the set. A leading `lifecycle_stage = null` (today's
detector pass hasn't completed yet) is treated as a one-step carry-forward
from the most recent prior non-null stage; nulls deeper in the walk are
treated as breaks. The 24-h carry-forward window matches the hourly detector's
worst-case latency budget.

$$
\text{streak} = \begin{cases}
0, & \text{if effective\_today} \notin \{1,2,3\} \\
1 + \sum_{i=1}^{N} \mathbb{1}[s_i \in \{1,2,3\}], & \text{otherwise}
\end{cases}
$$

where the sum runs while `s_i` is a confirmed emerging-stage day with no
break.

**`first_emerged_at`** — the `bucket_date` of the oldest day in the current
streak (equivalent to `today − stage_streak_days + 1`, stored explicitly so
the UI can render "Since May 4" without date arithmetic). `null` when
`stage_streak_days = 0`.

**`acs_slope_14d`** — ordinary least-squares slope of ACS against day index
over today + up to 13 prior daily snapshots, in *ACS-points-per-day*.
Positive slope = rising trend. Computed via `numpy.polyfit(deg=1)` over the
$(x_i, y_i)$ pairs where $x_i = -\text{days\_ago}_i$ (so a positive slope is
forward-in-time positive) and $y_i = \text{acs}_i$. Days where prior `acs` is
`null` are skipped (not zero-imputed). `null` when fewer than 5 valid samples
are available — the same floor used by the §5.6 bootstrap CI.

These fields are surfaced on the existing `/api/narrative/tickers/top` and
`/api/narrative/emerging` responses; no new endpoint. The frontend renders
them as two sortable columns (Streak, Trend) on the Emerging panel, with
three filter chips (New / Sustaining / Fading) over the same rows. The
default `decay_acs` sort is unchanged.

The 90-day TTL on `ticker_timeline` (per `infra/modules/cosmos.bicep`) is a
hard ceiling on `stage_streak_days`. Raising it is a follow-up — see
[ADR-0023](adr/0023-emerging-continuity-fields.md) "Risks".

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

ACS Component E captures signals (1)–(3) at scoring time. Each sub-signal is
fetched fresh every scorer run by `workers/scorer/market_confirmation.py` and
normalized to $[0, 1]$ before being combined with the weights in §5.1. See that
section for the normalization curve for each sub-signal. Signals (4)–(6) have
no implementation in either the scorer or the Narrative tab — there is no
manual-flag UI in `TickerDetailPanel.tsx` today.

**Avoid using price action as the primary signal.** A stock that is up 30% has
already confirmed, and is probably no longer in stages 1–3.

---

## 7. Failure modes and mitigations

| # | Mode | Signals | Mitigation |
|---|---|---|---|
| 1 | Coordinated manipulation | Sudden volume spike, $G > 0.65$, identical phrasing | Gini penalty (0.6×); pre-flight Cosmos query skips `post_id`s already extracted (eliminates duplicate OpenAI calls from the 6h look-back window); Cosmos `upsert_item` with `id=post_id_ticker` is the final dedup backstop; author-weight floor |
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
- Code: `job-ingestor` (always-on, MinReplicas=1, MaxReplicas=2); Arctic Shift
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
  `NOT IS_DEFINED(c.conviction_direction)`; structured-output JSON; prompt template
  stored in Key Vault as `conviction-prompt-v1`. Embedding generation runs in
  the same job (see Phase 5); an embedding failure does **not** block the
  conviction-axis write — see
  [ADR-0018](adr/0018-classifier-embedding-soft-fail.md).
- **No Azure ML.** Fine-tune escalation gated on F1 < 0.78
- Test: F1 ≥ 0.78 on direction-axis accuracy against a 300-post eval set

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
- Code: `job-acs-scorer` (20-min cron — bumped from 15 min in Phase 6.0.1 for
  a safety buffer against classifier/aggregator overlap);
  `scripts/backtest_narrative.py` + optional `job-backtest`; FastAPI routes
  under `/api/narrative/*`; Static Web Apps Narrative tab
- Test:
  - Backtest IC ≥ 0.04 at T+30 on held-out 90 days
  - `GET /api/narrative/tickers/top` p99 < 200ms with Redis warm
  - App Insights: `acs_staleness_seconds < 900` for ≥99% of any 24h window
  - Frontend smoke: synthetic high-ACS ticker visible within 30 minutes

---

## Change log

- **2026-05-18** — ADR-0023 continuity fields (code + doc):
  - **Code (scorer)**: added `compute_continuity_fields()` pure function and `ScorerCosmosClient.fetch_history()` single-partition read. The scorer now writes `stage_streak_days`, `first_emerged_at`, and `acs_slope_14d` alongside `acs` on every run.
  - **Code (backend)**: extended `AcsScore` dataclass, `_doc_to_acs` mapper, and `AcsScoreOut` Pydantic model to surface the new fields on `/api/narrative/tickers/top` and `/api/narrative/emerging` (no new endpoint).
  - **Code (frontend)**: `NarrativeTickerTable` gains two sortable columns (Streak, Trend) and three filter chips (New / Sustaining / Fading) on the Emerging panel. Default `decay_acs` sort unchanged.
  - **Doc (§5.7 new)**: defines the three continuity fields, their math, and the 24-h carry-forward rule for `null` lifecycle stages. Notes 90 d TTL as hard ceiling on streak.

- **2026-05-15e** — §7 dedup correction + pre-flight gate (code + doc):
  - **Code (extractor)**: added `CosmosWriter.get_extracted_post_ids()` — one cross-partition query per job run that returns which `post_id`s already have signals in Cosmos. `main.py` skips those posts entirely before calling OpenAI. Eliminates duplicate API calls from the ingestor's 6h look-back window re-publishing already-extracted posts. Query failure falls back to an empty set (safe — worst case is one redundant OpenAI call, not data loss). New log field: `skipped_dedup`.
  - **Doc fix (§7 row 1)**: updated mitigation to reflect the two-layer dedup: pre-flight query (prevents wasted OpenAI calls) + Cosmos upsert backstop (prevents duplicate documents). Removed the stale `Postgres UNIQUE (body_sha256, hour_bucket)` text.
- **2026-05-15d** — §6 market confirmation alignment pass (doc-only):
  - **Doc fix (§6)**: corrected the "Component E captures (1)–(3) at scoring
    time / (4)–(6) are flagged manually in the Narrative tab" paragraph.
    Neither is true today: the scorer hardcodes `comp_e = 0.0`, the
    `rs_14d` / `opt_ratio` / `institutional_13f_change` fields are
    commented-out placeholders in `backend/services/narrative/types.py`,
    and `TickerDetailPanel.tsx` has no manual-flag UI for (4)–(6). Section
    now states design intent explicitly and points to Phase 6.1 (ADR-0019).
- **2026-05-15c** — §5 ACS alignment pass (doc-only, no code changes):
  - **Doc fix (§5.1)**: Component A formula now shows the explicit $\min(\cdot, 1)$
    clip and uses $A_{\max}$ (KV-overridable) rather than the hardcoded 25.
  - **Doc fix (§5.1)**: Component B formula now shows the $\min(\cdot, B_{\max})$
    cap that the scorer applies (line 91 of `workers/scorer/scorer.py`) — the
    raw $\text{authors}/\log(\text{mentions})$ ratio can exceed 1 for low
    mention counts. Also documents the $\text{mentions}_{14d} \le 1$ short-circuit.
  - **Doc fix (§5.1)**: Component C formula now divides by $\max(\text{stage\_map})$
    so the component scales correctly if $C_{\max}$ is recalibrated via Key
    Vault (§5.5). Notes the module-load invariant assertion.
  - **Doc fix (§5.1)**: Component E row explicitly labelled "not yet
    implemented" — the scorer hardcodes `comp_e = 0.0`. Matches the §4 stage 4
    treatment.
  - **Doc fix (§5.3)**: clarified that the small-cap haircut requires
    $0 < \text{market\_cap} < \$100\text{M}$ — missing or zero market cap
    (yfinance lookup failed, non-equity ticker) does **not** trigger the
    penalty. Avoids punishing scores for absent data.
  - **Doc fix (§5.6)**: documented the $\pm 15\%$ heuristic fallback (when
    `daily_buckets` < 5) and the CI-bracketing clamp that guarantees
    `lower ≤ acs ≤ upper`.
  - **Doc fix (§8 Phase 6)**: cron updated from 15-min to 20-min to match the
    Bicep deployment (`infra/modules/containerapps.bicep` line 344) after the
    Phase 6.0.1 safety-buffer bump.
- **2026-05-15b** — §4 lifecycle alignment pass (doc + tests, no code changes):
  - **Doc fix (§4)**: added stage 0 ("insufficient data"), documented the
    `1 → 2 → 3 → 5 → 6` override priority (last matching rule wins), the
    catch-all stage-1 fallback at `0.4 × dominant_fraction`, and the per-stage
    confidence multipliers consumed by §5 Component C.
  - **Doc fix (§4)**: marked stage 4 explicitly "not yet implemented" — the
    `external_media_citations` and `analyst_name_count` fields it requires
    don't exist on `ticker_timeline` yet, so stage 4 is unreachable. Deferred
    to Phase 6.1.
  - **Doc fix (§4)**: clarified that `tier2_pct` / `tier3_pct` are persisted
    for the drilldown but not consumed by stage logic; stage 3 uses
    `contributor_count_growth_7d` as the rising-tier-2 proxy.
  - **Tests (workers/narrative-detector)**: added 8 `assign_stage` unit tests
    covering stage-0 short-circuit, each of stages 1/2/3/5/6, override
    priority (stage 3 beats stage 1), saturation overrides consensus, the
    catch-all path, and confidence scaling with `dominant_fraction`. Previous
    coverage of `assign_stage` was zero; now 12 detector tests pass.
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
