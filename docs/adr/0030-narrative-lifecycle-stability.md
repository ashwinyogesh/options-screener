# ADR-0030 — Narrative lifecycle stability via smoothed inputs + monotone hysteresis

* **Date:** 2025-04-13
* **Status:** Accepted
* **Supersedes (§4 only):** ADR-0017 (Narrative Phase 5 detector)
* **Touches:** [docs/NARRATIVE_METHODOLOGY.md §4](../NARRATIVE_METHODOLOGY.md#4-narrative-lifecycle)

## Context

The hourly narrative detector (`workers/narrative-detector`) computes
`lifecycle_stage ∈ {0..6}` per ticker by running HDBSCAN over the 72h
embedding window and then applying boolean rules against the ticker's
`ticker_timeline` bucket.

Operating-experience problem: stages were **visibly wobbling**. The same
ticker would read Stage 1 in one run, Stage 3 the next, and back to Stage 1
the run after — sometimes universe-wide flips at the 00:00 UTC bucket
rollover. Four structural causes:

1. **No input smoothing.** Inputs like `contributor_count_growth_7d` are
   point-in-time ratios against bucketed history. They jump discontinuously
   at the day boundary (the 7-day window shifts by one bucket) and after
   any single noisy aggregator run.
2. **No state.** `assign_stage` was a pure function of today's snapshot; the
   detector forgot its own previous output. The Stage 3 override rule
   (`contributor_count_growth_7d ≥ 0.30`) was unconditional, so a single
   noisy hour could promote a ticker from Stage 1 to Stage 3.
3. **Rule shape was bimodal.** Stage 2 required a 3-way AND
   (`tier1_pct ∈ [0.20, 0.50] AND dd_post_ratio ≥ 0.10 AND gini < 0.45`),
   nearly unreachable. In practice the detector produced either the
   Stage 1 catch-all or the Stage 3 override, with little in between.
4. **Day-rollover cliff.** Detector wrote to today's bucket, but most
   inputs were rolling-window aggregates anchored on the same day. At
   00:00 UTC the window shifted, every metric stepped, and a population
   of tickers flipped stage simultaneously.

The user mandate was explicit: **"let's not do patch work, let's fix it
once and for all"** — so we address all four causes in a single change.

## Decision

Replace the boolean-rule body of `assign_stage` with a four-stage pipeline:
**EMA smoothing → continuous breadth score → score-band mapping →
monotone hysteresis**. Stages 5/6 retain their explicit axis-share rules
because they describe a separate dimension (consensus posture), not a
position on the breadth axis. Stage 0 (insufficient data) preserves prior
state rather than overwriting it.

### Pipeline

1. **EMA smoothing** with `alpha = 0.4` (half-life ≈ 3 detector runs,
   ≈ 3 hours at the hourly cron). Applies to the 8 volatile inputs (see
   `SMOOTHED_KEYS` in `workers/narrative-detector/smoothing.py`). Cold
   start takes the first reading as-is. Missing inputs preserve prior
   smoothed value (no regression on aggregator gaps).

2. **Continuous breadth score** in roughly $[0, 1]$:

   $$ \text{breadth} = 0.5 \cdot \widetilde{\text{tier1}} + 0.3 \cdot \text{clip}\!\left(\frac{\widetilde{\text{growth}}}{0.5}, 0, 1\right) + 0.2 \cdot \widetilde{\text{dd\_post}} $$

3. **Score-band mapping** (Stages 1/2/3):
   `< 0.15` → 1, `[0.15, 0.35)` → 2, `≥ 0.35` → 3.
   Stage 5/6 overlay replaces the breadth target when its axis condition
   holds against the **smoothed** axis shares.

4. **Monotone hysteresis** — cap movement to ±1 stage per commit and
   require `confirm_runs = 2` consecutive observations of the new target
   before committing. State carried on the bucket
   (`lifecycle_state.pending_stage`, `lifecycle_state.pending_streak`)
   tracks the candidate transition. Cold start (`prev_stage == 0`)
   accepts target immediately.

### State persistence

A new `lifecycle_state` object is written to every timeline bucket
alongside the existing `lifecycle_stage` / `stage_confidence` fields:

```jsonc
"lifecycle_state": {
  "smoothed_inputs": { "tier1_pct": 0.34, "...": "..." },
  "pending_stage": 3,
  "pending_streak": 1
}
```

Read order at start of each detector run: (1) today's bucket if a previous
same-day run wrote `lifecycle_state`; (2) yesterday's bucket; (3) cold
start. This keeps hysteresis updating hour-by-hour rather than only at
day rollovers.

### Confidence

$$ \text{confidence} = \text{dominant\_fraction} \cdot \text{certainty} \cdot \text{proximity} $$

`certainty = 1.0` when committed == target, `0.5` mid-transition.
`proximity ∈ [0, 1]` decays to 0 at band boundaries — scores sitting
exactly on a threshold are reported with low confidence to flag potential
imminent flips.

## Consequences

### Positive

- **No more universe-wide flips at 00:00 UTC.** EMA smoothing dampens the
  rolling-window step; hysteresis blocks single-run-induced transitions.
- **Stage 2 is reachable.** Replacing the 3-way AND with a continuous
  score means moderate-breadth narratives land in Stage 2 instead of
  defaulting to the Stage 1 catch-all.
- **Stage transitions are auditable.** `pending_stage` / `pending_streak`
  on each bucket reveals what the detector is considering and why a move
  hasn't committed yet.
- **No new Cosmos container.** State piggy-backs on the existing
  `ticker_timeline` bucket; no migration required.
- **Worst-case latency for 1 → 3 jump: 4 hours.** Acceptable for a
  narrative-development signal (the underlying dynamic doesn't happen
  faster than that anyway).

### Negative

- **EMA introduces lag.** A genuine sudden narrative explosion takes
  ~3 runs to fully reflect in the smoothed inputs and ≥4 to walk from
  Stage 1 to Stage 3. The ACS scorer in §5 still consumes the raw
  `contributor_count_growth_7d` as a separate factor (component E), so
  acute spikes are not lost — they just don't move the lifecycle stage
  on their own.
- **Cold start asymmetry.** A ticker with no prior `lifecycle_state`
  jumps immediately to its target stage on first observation. This is
  intentional — we don't want a 4-hour warm-up period for newly active
  tickers — but it does mean the very first reading is unsmoothed.
- **Behaviour change in tests.** All previous boolean-rule tests for
  `assign_stage` are rewritten. The new test suite in
  `workers/narrative-detector/tests/test_smoothing.py` covers each
  pipeline stage in isolation; `tests/test_detector.py::TestAssignStage`
  covers the integration.

### Rollout

- Backwards-compatible: timeline docs without `lifecycle_state` are
  treated as cold starts; old `lifecycle_stage` values are read as
  `prev_stage` so hysteresis kicks in on the second run after deploy.
- No migration needed. Existing buckets retain their values; new buckets
  start carrying `lifecycle_state` immediately.

## Alternatives considered

- **State container.** Considered adding a `narrative_lifecycle_state`
  Cosmos container partitioned by ticker. Rejected — state is naturally
  per-bucket, RU cost is the same, and an extra container would
  bifurcate the audit trail.
- **Apply hysteresis to overlay (5/6) separately.** Considered tracking a
  separate `overlay_pending` field so consensus/saturation moves don't
  block breadth-stage progression. Rejected for v1 — the single
  pending channel works because overlay transitions are rare; revisit
  if Stage 5 ↔ Stage 6 oscillation is observed in production.
- **Bayesian update.** A proper Kalman filter over breadth score would
  give a principled confidence. Overkill at current data quality;
  reconsider if we adopt continuous-time signal latency tracking.

## References

- [workers/narrative-detector/smoothing.py](../../workers/narrative-detector/smoothing.py) — pure pipeline functions
- [workers/narrative-detector/detector.py](../../workers/narrative-detector/detector.py) — `assign_stage` orchestrator
- [workers/narrative-detector/tests/test_smoothing.py](../../workers/narrative-detector/tests/test_smoothing.py) — pipeline unit tests
- [ADR-0017](0017-narrative-phase5-detector.md) — the boolean-rule design this supersedes (§4 only)
- [docs/NARRATIVE_METHODOLOGY.md §4](../NARRATIVE_METHODOLOGY.md#4-narrative-lifecycle)


---

## Amendment — Decoupled stage gate (GOOGL fix)

**Date:** 2025-11-21
**Status:** Accepted

### Problem

Polysemic megacaps (GOOGL, AMZN, META, AAPL) with 15–20 embedded posts
spanning multiple sub-themes (cloud / AI / Waymo / antitrust for GOOGL)
were dropping to `---` (stage 0) in the UI.

Root cause: the original gate in `assign_stage` skipped stage
assignment when `cluster_result.n_clusters == 0`. HDBSCAN, with the
intra-cluster similarity floor of 0.35 introduced in ADR-0026, demotes
semantically diverse clusters to noise — so a ticker with abundant
signal but no single dominant sub-narrative produced
`n_clusters == 0` and was treated as `insufficient data`.

HDBSCAN's job is to measure narrative *coherence*. It should not also
decide whether a ticker classifies at all.

### Decision

Decouple the stage gate from HDBSCAN by introducing two separate
quantities on `ClusterResult`:

- `n_embedded` — count of signals that survived embedding (signal
  *volume*).
- `dominant_fraction` — already present; share of non-noise signals in
  the largest cluster (signal *coherence*).

New gate constants (`workers/narrative-detector/detector.py`):

| Constant | Value | Role |
|---|---|---|
| `N_MIN_EMBEDDED` | `5` | Floor for stage classification |
| `N_VOLUME_FULL` | `10` | Volume at which confidence factor saturates |
| `COHERENCE_FLOOR` | `0.3` | Minimum effective `dominant_fraction` |

`assign_stage` now:

1. Returns stage 0 only when `n_embedded < N_MIN_EMBEDDED`.
2. Floors `dominant_fraction` at `COHERENCE_FLOOR` before feeding it
   into `compute_confidence` (so polysemic clusters still get usable,
   not-zero confidence).
3. Multiplies the base confidence by
   `volume_factor = min(n_embedded / N_VOLUME_FULL, 1.0)` so a thin
   3-post cluster is reported at 30% of a 10-post cluster's confidence.

### Effect

- **GOOGL** (n_embedded≈15, n_clusters=0, dom_frac≈0.0): now classifies
  to its breadth-driven stage at ~0.45 confidence instead of `---`.
- **Coherent small-caps** (n_embedded=8, n_clusters=1, dom_frac=0.9):
  unchanged — high coherence and saturated-ish volume keep confidence
  high.
- **Genuinely thin signal** (n_embedded < 5): still returns stage 0 with
  prior-state preservation. No regression in noise rejection.

### Persistence

`cosmos_client.write_lifecycle` now accepts optional `n_embedded` and
`dominant_fraction` kwargs and persists both on the timeline bucket
doc. `main.py` plumbs them through. These fields are intended for
drilldown UI surfacing so users can see *why* a stage came in at a given
confidence.
