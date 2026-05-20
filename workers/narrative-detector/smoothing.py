"""Lifecycle stage stability layer — input smoothing + monotone hysteresis.

Implements ADR-0029 (Narrative lifecycle stability):

    Step 1  EMA-smooth the volatile aggregator inputs.
    Step 2  Compute a continuous breadth score from smoothed inputs.
    Step 3  Map score to a target stage in {1, 2, 3} via fixed bands.
    Step 4  Optionally override with stage 5/6 overlay when axis conditions hold.
    Step 5  Apply monotone hysteresis: cap movement to ±1 stage / commit,
            require ``confirm_runs`` consecutive observations of the new target.

Pure module — no I/O, no Cosmos imports.  Consumed by ``detector.assign_stage``
and unit-tested in ``tests/test_smoothing.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Tunables — keep methodology doc and tests in lockstep with these constants.
# ---------------------------------------------------------------------------

# EMA smoothing factor for volatile inputs.  alpha=0.4 → ~3-day half-life with
# hourly detector runs (one EMA step per run).  Higher alpha = more reactive,
# lower = more stable.
EMA_ALPHA: float = 0.4

# Breadth-score band thresholds.  Computed empirically from §4 methodology.
STAGE1_MAX: float = 0.15   # below this → niche
STAGE2_MAX: float = 0.35   # between → early conviction; above → expanding

# Number of consecutive detector runs the new target must be observed before a
# stage transition is committed.  Higher = more stable, slower to react.
DEFAULT_CONFIRM_RUNS: int = 2

# Volatile inputs subject to EMA smoothing.  Listed here so the smoothing
# layer never accidentally smooths a metric that should pass through.
SMOOTHED_KEYS: tuple[str, ...] = (
    "tier1_pct",
    "tier2_pct",
    "gini_14d",
    "dd_post_ratio",
    "financial_term_density",
    "contributor_count_growth_7d",
    "conviction_bull_share",
    "conviction_researched_share",
)


# ---------------------------------------------------------------------------
# State container
# ---------------------------------------------------------------------------

@dataclass
class LifecycleState:
    """Persisted hysteresis state — round-trips through the timeline doc.

    Stored as an object under ``lifecycle_state`` on each ticker_timeline
    bucket.  Opaque to other workers; only the detector reads/writes it.
    """

    smoothed_inputs: dict[str, float] = field(default_factory=dict)
    pending_stage: int = 0       # 0 = no pending move
    pending_streak: int = 0      # consecutive runs target == pending_stage

    @classmethod
    def from_doc(cls, doc: dict[str, Any] | None) -> "LifecycleState":
        if not doc:
            return cls()
        raw = doc.get("lifecycle_state") or {}
        return cls(
            smoothed_inputs=dict(raw.get("smoothed_inputs") or {}),
            pending_stage=int(raw.get("pending_stage") or 0),
            pending_streak=int(raw.get("pending_streak") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "smoothed_inputs": self.smoothed_inputs,
            "pending_stage": self.pending_stage,
            "pending_streak": self.pending_streak,
        }


# ---------------------------------------------------------------------------
# Step 1 — EMA smoothing
# ---------------------------------------------------------------------------

def ema_smooth(
    raw: dict[str, Any],
    prior: dict[str, float],
    alpha: float = EMA_ALPHA,
) -> dict[str, float]:
    """EMA-smooth the volatile inputs.

    Args:
        raw: today's aggregator output (full timeline doc; only SMOOTHED_KEYS
            are consumed, other fields ignored).
        prior: smoothed values from the previous detector run.  Empty dict on
            cold start.
        alpha: EMA factor in (0, 1].  Defaults to ``EMA_ALPHA``.

    Returns:
        New smoothed dict.  Keys present in ``prior`` but missing from ``raw``
        are passed through unchanged (preserve state across aggregator gaps).
    """
    out: dict[str, float] = dict(prior)
    for key in SMOOTHED_KEYS:
        new_val = raw.get(key)
        if new_val is None:
            # Aggregator hasn't computed this metric in this bucket yet.
            # Preserve prior smoothed value (no regression).
            continue
        new_val = float(new_val)
        prev = prior.get(key)
        if prev is None:
            out[key] = new_val           # cold start: trust the first reading
        else:
            out[key] = alpha * new_val + (1.0 - alpha) * float(prev)
    return out


# ---------------------------------------------------------------------------
# Step 2 — Continuous breadth score
# ---------------------------------------------------------------------------

def breadth_score(smoothed: dict[str, float]) -> float:
    """Continuous narrative-breadth score in roughly [0, 1].

    Higher = wider mainstream attention with deeper substance.  The score is
    designed so that smoothly increasing each input lifts the score smoothly —
    no thresholds, no AND-gates.

    Weights (sum=1.0):
        0.5  tier1_pct                              — mainstream contributor share
        0.3  clip(contributor_growth / 0.5, 0..1)   — week-over-week expansion
        0.2  dd_post_ratio                          — substance / due-diligence
    """
    tier1 = float(smoothed.get("tier1_pct") or 0.0)
    growth = float(smoothed.get("contributor_count_growth_7d") or 0.0)
    dd = float(smoothed.get("dd_post_ratio") or 0.0)
    growth_norm = max(0.0, min(growth / 0.5, 1.0))
    return 0.5 * tier1 + 0.3 * growth_norm + 0.2 * dd


# ---------------------------------------------------------------------------
# Step 3 — Score → stage band
# ---------------------------------------------------------------------------

def breadth_to_stage(score: float) -> int:
    """Map continuous breadth score to discrete breadth stage 1, 2, or 3."""
    if score < STAGE1_MAX:
        return 1
    if score < STAGE2_MAX:
        return 2
    return 3


# ---------------------------------------------------------------------------
# Step 4 — Stage 5/6 overlay (axis-based, not breadth-based)
# ---------------------------------------------------------------------------

def overlay_stage(smoothed: dict[str, float]) -> int | None:
    """Return 5 or 6 if the axis-share overlay condition holds, else None.

    Stage 5 (Consensus):     bull ≥ 0.65 AND researched < 0.40 AND gini < 0.30
    Stage 6 (Saturation):    bull ≥ 0.75 AND researched < 0.30 AND gini ≥ 0.55

    Inputs are smoothed shares from the axis classifier (ADR-0020 / ADR-0021).
    Both shares must be present (i.e., axis-classified signals exist) for the
    overlay to fire.  When axis data is absent, overlay returns None and the
    breadth stage stands.
    """
    bull = smoothed.get("conviction_bull_share")
    researched = smoothed.get("conviction_researched_share")
    gini = float(smoothed.get("gini_14d") or 0.0)
    if bull is None or researched is None:
        return None
    bull = float(bull)
    researched = float(researched)
    if bull >= 0.75 and researched < 0.30 and gini >= 0.55:
        return 6
    if bull >= 0.65 and researched < 0.40 and gini < 0.30:
        return 5
    return None


# ---------------------------------------------------------------------------
# Step 5 — Monotone hysteresis
# ---------------------------------------------------------------------------

def apply_hysteresis(
    target: int,
    prev_stage: int,
    state: LifecycleState,
    *,
    confirm_runs: int = DEFAULT_CONFIRM_RUNS,
) -> tuple[int, LifecycleState]:
    """Apply monotone hysteresis to a desired stage transition.

    Behaviour:
        * Cold start (``prev_stage == 0``) — accept target immediately.
        * ``target == prev_stage`` — held, reset any pending counter.
        * ``target != prev_stage`` — accumulate ``pending_streak``; when it
          reaches ``confirm_runs``, commit a single ±1 stage move toward
          target.  This means a 1 → 3 jump takes a minimum of 4 detector runs
          (2 to confirm 1→2, then 2 to confirm 2→3).

    Returns:
        (committed_stage, new_state).  ``smoothed_inputs`` on ``new_state`` is
        copied verbatim from ``state`` — callers are expected to update it
        separately before persisting.
    """
    if prev_stage == 0:
        return target, LifecycleState(smoothed_inputs=state.smoothed_inputs)

    if target == prev_stage:
        return prev_stage, LifecycleState(
            smoothed_inputs=state.smoothed_inputs,
            pending_stage=0,
            pending_streak=0,
        )

    new_streak = state.pending_streak + 1 if state.pending_stage == target else 1

    if new_streak >= confirm_runs:
        direction = 1 if target > prev_stage else -1
        committed = prev_stage + direction
        return committed, LifecycleState(
            smoothed_inputs=state.smoothed_inputs,
            pending_stage=0,
            pending_streak=0,
        )

    return prev_stage, LifecycleState(
        smoothed_inputs=state.smoothed_inputs,
        pending_stage=target,
        pending_streak=new_streak,
    )


# ---------------------------------------------------------------------------
# Confidence calculation
# ---------------------------------------------------------------------------

def compute_confidence(
    score: float,
    target_stage: int,
    committed_stage: int,
    dominant_fraction: float,
) -> float:
    """Confidence = dominant_fraction × certainty × band-center proximity.

    * dominant_fraction comes from the cluster() result.
    * certainty drops to 0.5 when committed stage trails target (mid-transition).
    * proximity peaks at the band centre, falls toward 0 at boundaries —
      values right on a threshold get lower confidence to signal uncertainty.
    """
    certainty = 1.0 if committed_stage == target_stage else 0.5

    if score < STAGE1_MAX:
        center = STAGE1_MAX / 2.0
        half_width = STAGE1_MAX / 2.0
    elif score < STAGE2_MAX:
        center = (STAGE1_MAX + STAGE2_MAX) / 2.0
        half_width = (STAGE2_MAX - STAGE1_MAX) / 2.0
    else:
        # Stage 3 band has no upper bound; use 0.15 half-width above STAGE2_MAX.
        center = STAGE2_MAX + 0.15
        half_width = 0.15

    proximity = max(0.0, 1.0 - abs(score - center) / max(half_width, 1e-6))
    raw = dominant_fraction * certainty * proximity
    return round(max(0.0, min(1.0, raw)), 4)
