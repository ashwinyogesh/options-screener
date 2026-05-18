# ADR-0023: Emerging-tab continuity fields

- Status: Accepted
- Date: 2026-05-18
- Supersedes: —
- Related: [ADR-0019](0019-narrative-phase6-scorer.md), [ADR-0021](0021-retire-legacy-conviction-taxonomy.md)

## Context

The Narrative tab today is a point-in-time view. The Top-ACS and Emerging
tables both render the newest `ticker_timeline` snapshot per ticker — they
do not expose how long a ticker has been emerging, whether its ACS is
trending up or down, or when its current stage 1–3 run began.

Two concrete user-visible problems motivate this change:

1. **One-day-spike confusion.** A ticker that briefly entered stage 2 today
   ranks next to a ticker that has held stage 2 for three weeks. Users cannot
   tell durable narratives from noise without clicking each row.
2. **Cross-panel inconsistency after ADR-0021 deploy.** Tickers whose newest
   snapshot lacked a lifecycle stage (detector hadn't run yet) were silently
   served stale older snapshots by `query_emerging`, producing the same
   ticker with different ACS / stage in Top vs Emerging. The dedup-then-filter
   fix (commit `8a3cf7f`) resolves the inconsistency but exposes the deeper
   gap: the UI has no representation of continuity, so users can't reason
   about why a ticker disappeared from one panel.

The data needed to surface continuity already lives in Cosmos
(`ticker_timeline`, 90-day TTL, partition `/ticker`). What is missing is
pre-computed scalar fields on today's snapshot so the read path doesn't
have to fetch and reduce history per request.

## Decision

**Add three continuity fields to the `ticker_timeline` schema, written by
the ACS scorer on every run.**

| Field | Type | Definition |
|---|---|---|
| `stage_streak_days` | int | Consecutive days ending today where `lifecycle_stage ∈ {1, 2, 3}`. A day with `lifecycle_stage = None` is treated as carry-forward from the prior day for up to 24h (so a mid-morning render before the hourly detector run does not zero the streak). Any day where the most recent non-null stage was outside `{1, 2, 3}` resets the streak to 0. |
| `first_emerged_at` | str (ISO date) \| null | Earliest `bucket_date` within the current streak. `null` when `stage_streak_days = 0`. Equivalent to `today - stage_streak_days + 1` but stored explicitly so the UI can display "Since May 4" without date arithmetic. |
| `acs_slope_14d` | float \| null | OLS slope of `acs` against day index over the last 14 daily snapshots, including today. Units: ACS-points-per-day. `null` when fewer than 5 prior daily docs exist (insufficient sample). |

The scorer computes these once per ticker per run by reading the prior 30
daily docs for that ticker (single-partition query, cheap), then writes them
alongside the existing `acs`, `decay_acs`, `acs_components`, `acs_flags`,
`acs_scored_at` fields.

The fields are surfaced on the existing `/api/narrative/tickers/top` and
`/api/narrative/emerging` responses — no new endpoint. The frontend adds
two sortable columns (Streak, Slope) and three filter chips (New /
Sustaining / Fading) over the same rows.

### Filter chip semantics (frontend only — not stored)

- **New** — `stage_streak_days <= 7`.
- **Sustaining** — `stage_streak_days >= 14 AND acs_slope_14d >= 0`.
- **Fading** — `acs_slope_14d < 0` OR last stage transition was 3 → {4,5,6}.

These are client-side filters over the rows already returned by `/emerging`.
They are *not* alternative ranking functions — the default sort stays
`decay_acs` desc.

## Why this shape

### Why three fields, not a composite continuity score

An earlier sketch combined ACS, streak, slope and CI-width into a
z-scored composite. Two problems killed it:

1. **Universe size.** At ~100 active tickers the per-day z-score is noisy;
   ranking would wobble run-to-run for no underlying reason.
2. **Overlap with `decay_acs`.** The scorer already applies an exponential
   staleness decay (λ=0.07/day, half-life ≈10 days). Adding another decay
   on top distorts the ACS calibration documented in
   [NARRATIVE_METHODOLOGY.md](../NARRATIVE_METHODOLOGY.md) §5.

Surfacing the raw scalars and letting users sort/filter on them avoids
both: ACS keeps its single calibrated meaning, and continuity is exposed
as orthogonal axes the user can rank on directly.

### Why the scorer, not the aggregator

Both touch `ticker_timeline` and either could host this work. The scorer is
preferred because:

- The slope is over `acs` values, which only the scorer writes. Computing it
  there avoids a circular dependency where the aggregator would need to wait
  for yesterday's scorer to finish before computing today's slope.
- The scorer's `write_acs` is the only writer that already carries today's
  fresh ACS in memory; computing slope at that point includes today's value
  naturally.
- The aggregator's `_PRESERVE_FIELDS` set in
  [workers/aggregator/cosmos_writer.py](../../workers/aggregator/cosmos_writer.py)
  would have to grow if these fields were aggregator-owned. Keeping them
  scorer-owned mirrors the existing ownership of `acs` and `decay_acs`.

### Why carry-forward null stage for 24h, not break the streak

The detector runs hourly; the aggregator runs every 15 min. Between the
midnight bucket rollover and the first detector run of the day, today's
snapshot has `lifecycle_stage = null`. Without carry-forward, a ticker
emerging for 30 days would show `stage_streak_days = 0` for ~1 hour each
morning, then snap back to 31 — a UX cliff for no underlying change. The
24h carry-forward window matches the detector's worst-case latency budget.

### Why a 14-day slope window, not 7 or 30

- 7 days is too short — single-week ACS noise dominates the slope.
- 30 days exceeds the typical lifecycle of a stage-1→3 narrative; a slope
  measured over 30 days mixes the rising and falling halves of the same
  narrative.
- 14 days matches the existing attention persistence window
  (`decay_weighted_density_14d`) so a single user-visible interpretation —
  "two-week trend" — applies to both fields.

### Why null below 5 daily docs

Below 5 samples, OLS slope is statistically meaningless (high standard
error, sensitive to outliers). The frontend renders null as `—` rather than
risk showing a misleading trend arrow on a 3-day-old ticker. The same
5-day threshold is already used by the bootstrap CI in
[workers/scorer/scorer.py](../../workers/scorer/scorer.py).

## Consequences

### Positive

- Users can distinguish multi-week durable narratives from one-day spikes
  at a glance, on the same table they already use.
- The default sort (`decay_acs`) is unchanged, so existing user habits and
  the published methodology remain valid.
- No new endpoint, no new container, no client-side history fetch — the
  read path stays as cheap as it is today.
- Cross-panel inconsistency from ADR-0021's wake is fully resolved when
  combined with commit `8a3cf7f` (dedup-then-filter).

### Negative

- Adds three Cosmos read operations per ticker per scorer run (single
  partition, ~30 docs). At ~150 active tickers × ~3 scorer runs per hour
  this is ~13,500 reads/hour — negligible on Cosmos Serverless.
- Backfilling continuity for the 90-day historical window requires either
  a one-shot script or waiting for natural scorer cycles to populate it
  (every ticker re-scored every 20 min → fully populated within hours).

### Risks

- **Streak instability** if the detector backlog grows beyond 24h. The
  carry-forward window is finite; a multi-day detector outage would still
  zero streaks. Acceptable — the existing `acs_staleness_seconds` alert
  fires long before that.
- **Slope volatility around stage transitions.** When a ticker moves from
  stage 2 → 3 the ACS often jumps via Component C; the slope reflects this
  as a single-day spike. Documented in the methodology update; no
  smoothing applied (would mask real transitions).

## Reversibility

Fully reversible. The fields are additive — removing them requires only:
1. Stop writing them in the scorer.
2. Remove them from the backend response model.
3. Remove the columns and chips from the frontend.

Existing docs do not need to be deleted; the fields just stop being
refreshed and decay out of the table as the 90-day TTL expires them.

## Operational notes

- No infra change. No KV secret. No new container.
- No prompt change. No model change.
- Tests added:
  - `workers/scorer/tests/test_scorer.py` — `compute_continuity_fields` pure function
  - `backend/tests/unit/test_narrative_read_service.py` — new fields surfaced
- Methodology doc updated in the same PR per the lockstep rule in
  [.github/copilot-instructions.md](../../.github/copilot-instructions.md).
