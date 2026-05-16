"""ACS computation — pure functions (Phase 6).

Implements NARRATIVE_METHODOLOGY.md §5.

Components:
    A  Attention persistence  — decay_weighted_density_14d * A_max
    B  Contributor quality   — unique_authors / log(mentions) * (1-G) * B_max
    C  Narrative strength    — stage_map[stage] * stage_confidence * (C_max / 20)
    D  Thesis quality        — (0.6*r_rb + 0.2*r_rB + 0.2*conv_norm) * D_max, floored at 0
    E  Market confirmation   — 0 (deferred to Phase 6.1)

Adjustments (multipliers, in order — §5.3):
    G > 0.65                                → × 0.6   (gini_high)
    3 consecutive days of decreasing mentions → × 0.8 (decelerating_3d)
    lifecycle_stage > 3                     → × 0.5   (late_stage)
    0 < market_cap < $100M                  → × 0.85  (small_cap)

CI bands: bootstrap percentile (resampling daily_buckets) when ≥5 days are
available; otherwise fall back to a ±15% heuristic.
Time decay: ACS(t) = ACS_raw * e^{-0.07 * t} where t = days since acs_scored_at.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

# stage_map per NARRATIVE_METHODOLOGY.md §5.1 — stages 2 and 3 are target window.
_STAGE_MAP: dict[int, float] = {1: 10, 2: 18, 3: 20, 4: 10, 5: 5, 6: 2}
# Invariant (§5.1): the peak of stage_map defines the denominator in Component
# C so that a perfectly-staged, fully-confident narrative scores exactly C_max.
# If this is ever broken (e.g. stage_map is rescaled), Component C silently
# saturates above or below C_max and the ACS becomes uncalibrated.
_STAGE_MAP_PEAK: float = max(_STAGE_MAP.values())
assert _STAGE_MAP_PEAK == 20.0, "Component C denominator (see §5.1) drifted from stage_map peak."
# Two distinct decay constants — do not collapse:
#   ACS_TIME_DECAY (§5.4): exponential staleness of a *score* over days since
#     it was computed. half-life ≈ 10 days.
#   ATTENTION_DECAY (§2.1): exponential weighting of *signals* by age inside
#     the 14-day attention window. half-life ≈ 6.9 days. Must match aggregator.
_ACS_TIME_DECAY_RATE: float = 0.07
_ATTENTION_DECAY_LAMBDA: float = 0.1
_WINDOW_14D: int = 14                   # component-A window length

# Bootstrap-CI parameters.
_BOOTSTRAP_N: int = 500
_BOOTSTRAP_MIN_DAYS: int = 5
_BOOTSTRAP_LOWER_PCT: float = 2.5
_BOOTSTRAP_UPPER_PCT: float = 97.5
_HEURISTIC_CI_HALFWIDTH: float = 0.15   # ± when bootstrap unavailable

# §5.3 thresholds.
_GINI_HIGH: float = 0.65
_LATE_STAGE: int = 3
_SMALL_CAP_USD: float = 100_000_000.0
_DECEL_STREAK_DAYS: int = 3


@dataclass
class AcsResult:
    ticker: str
    acs: float
    acs_ci_lower: float
    acs_ci_upper: float
    decay_acs: float
    components: dict[str, float]   # {A, B, C, D, E}
    dominant_signal: str
    flags: list[str] = field(default_factory=list)


def compute_acs(doc: dict, weights: dict[str, float]) -> AcsResult:
    """Compute ACS for a single ticker_timeline Cosmos document.

    Args:
        doc:     ticker_timeline document (may have partial Phase 3–5 fields).
        weights: component max weights from Key Vault (or defaults).

    Returns:
        AcsResult with all fields populated.
    """
    ticker: str = doc.get("ticker", "")
    a_max: float = weights.get("A_max", 25.0)
    b_max: float = weights.get("B_max", 20.0)
    c_max: float = weights.get("C_max", 20.0)
    d_max: float = weights.get("D_max", 20.0)

    # --- Component A: attention persistence ---
    dwd_14d: float = doc.get("decay_weighted_density_14d") or 0.0
    comp_a = min(dwd_14d, 1.0) * a_max

    # --- Component B: contributor quality ---
    unique_authors: int = doc.get("unique_authors_14d") or 0
    mentions_14d: int = doc.get("mentions_14d") or 0
    gini: float = doc.get("gini_14d") or 0.0
    if mentions_14d > 1 and unique_authors > 0:
        comp_b = (unique_authors / math.log(mentions_14d)) * (1.0 - gini) * b_max
        comp_b = min(comp_b, b_max)
    else:
        comp_b = 0.0

    # --- Component C: narrative strength (lifecycle) ---
    stage: int = doc.get("lifecycle_stage") or 0
    stage_conf: float = doc.get("stage_confidence") or 0.0
    if stage in _STAGE_MAP:
        comp_c = (_STAGE_MAP[stage] / _STAGE_MAP_PEAK) * stage_conf * c_max
    else:
        comp_c = 0.0

    # --- Component D: thesis quality ---
    # conv_norm is the §3 weighted-conviction mean over classified 14d signals,
    # range [-0.5, 1.0]. A wave of exit_signal posts can push thesis_score
    # negative; we floor comp_d at 0 so every ACS component is bounded in
    # [0, max] and calibration math stays well-behaved (§5.1).
    r_rb: float = doc.get("conviction_researched_bull_ratio") or 0.0
    r_rB: float = doc.get("conviction_researched_bear_ratio") or 0.0
    conv_norm: float = doc.get("conviction_dd_norm") or 0.0
    thesis_score = (0.6 * r_rb) + (0.2 * r_rB) + (0.2 * conv_norm)
    comp_d = max(0.0, min(thesis_score, 1.0)) * d_max

    # --- Component E: market confirmation (§5.1, §6) ---
    # Sub-signals are pre-populated by main.py via get_market_confirmation();
    # absent = 0.0 so the scorer degrades gracefully when yfinance is down.
    e_max: float = weights.get("E_max", 15.0)
    rs_norm: float = doc.get("rs_14d_norm") or 0.0
    opt_norm: float = doc.get("opt_ratio_norm") or 0.0
    inst_norm: float = doc.get("institutional_13f_norm") or 0.0
    comp_e = min(6.0 * rs_norm + 5.0 * opt_norm + 4.0 * inst_norm, e_max)

    acs_raw = comp_a + comp_b + comp_c + comp_d + comp_e

    # --- Adjustments (§5.3) ---
    multiplier = 1.0
    flags: list[str] = []

    if gini > _GINI_HIGH:
        multiplier *= 0.6
        flags.append("gini_high")

    if _is_decelerating_streak(doc.get("daily_buckets") or [], _DECEL_STREAK_DAYS):
        multiplier *= 0.8
        flags.append("decelerating_3d")

    if stage > _LATE_STAGE:
        multiplier *= 0.5
        flags.append("late_stage")

    market_cap = doc.get("market_cap")
    if isinstance(market_cap, (int, float)) and 0 < market_cap < _SMALL_CAP_USD:
        multiplier *= 0.85
        flags.append("small_cap")

    acs = min(100.0, max(0.0, acs_raw * multiplier))

    # --- CI bands (§5.6 bootstrap; fallback ±15% heuristic) ---
    acs_ci_lower, acs_ci_upper = _bootstrap_ci(
        doc=doc,
        comp_b=comp_b,
        comp_c=comp_c,
        comp_d=comp_d,
        comp_e=comp_e,
        a_max=a_max,
        multiplier=multiplier,
        acs=acs,
    )

    # --- Time decay ---
    computed_at_str: str = doc.get("computed_at") or doc.get("acs_scored_at") or ""
    days_stale = _days_since(computed_at_str)
    decay_acs = acs * math.exp(-_ACS_TIME_DECAY_RATE * days_stale) if days_stale > 0 else acs

    # --- Dominant signal ---
    dominant_signal = _dominant_signal(doc)

    return AcsResult(
        ticker=ticker,
        acs=round(acs, 4),
        acs_ci_lower=round(acs_ci_lower, 4),
        acs_ci_upper=round(acs_ci_upper, 4),
        decay_acs=round(decay_acs, 4),
        components={
            "A": round(comp_a, 4),
            "B": round(comp_b, 4),
            "C": round(comp_c, 4),
            "D": round(comp_d, 4),
            "E": round(comp_e, 4),
        },
        dominant_signal=dominant_signal,
        flags=flags,
    )


def _days_since(iso_str: str) -> float:
    """Return fractional days between iso_str and now. 0.0 if unparseable."""
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(tz=timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 86400)
    except ValueError:
        return 0.0


def _dominant_signal(doc: dict) -> str:
    """Return the conviction state label with the highest ratio, or 'unknown'."""
    candidates = {
        "researched_bull": doc.get("conviction_researched_bull_ratio") or 0.0,
        "researched_bear": doc.get("conviction_researched_bear_ratio") or 0.0,
        "emotional_bull":  doc.get("conviction_emotional_bull_ratio") or 0.0,
    }
    # Also consider raw sentiment if conviction hasn't run yet.
    if all(v == 0.0 for v in candidates.values()):
        bullish: float = doc.get("bullish_ratio") or 0.0
        bearish: float = doc.get("bearish_ratio") or 0.0
        if bullish > 0 or bearish > 0:
            return "bullish" if bullish >= bearish else "bearish"
        return "unknown"
    return max(candidates, key=lambda k: candidates[k])


def _is_decelerating_streak(daily_buckets: list, streak_days: int) -> bool:
    """True iff the trailing `streak_days` mention counts are strictly decreasing.

    Matches §5.3: "Acceleration negative for 3 days". Reading the raw daily
    bucket counts is a faithful expression — strictly monotone decrease over
    N consecutive days implies a negative derivative across that window.

    Args:
        daily_buckets: list of dicts with a "count" key, sorted ascending by day
                       (the aggregator writes them this way per §2.1).
        streak_days:   length of the required decreasing run (default 3).

    Returns:
        False when there are fewer than `streak_days` buckets.
    """
    if len(daily_buckets) < streak_days:
        return False
    counts = [int(b.get("count", 0)) for b in daily_buckets[-streak_days:]]
    return all(counts[i] > counts[i + 1] for i in range(streak_days - 1))


def _decay_weighted_density(daily_counts: list[int], window_days: int = _WINDOW_14D) -> float:
    """Recompute attention §2.1 density from a list of daily counts.

    Mirrors the aggregator's formula so the bootstrap is self-consistent.
    `daily_counts` is most-recent-last; t=0 is the last index.
    """
    if not daily_counts:
        return 0.0
    n = min(len(daily_counts), window_days)
    counts = daily_counts[-n:]
    weighted = 0.0
    for i, c in enumerate(counts):
        t = (n - 1) - i  # 0 = today, n-1 = oldest
        weighted += math.exp(-_ATTENTION_DECAY_LAMBDA * t) * c
    max_weight = sum(math.exp(-_ATTENTION_DECAY_LAMBDA * t) for t in range(window_days + 1))
    if max_weight <= 0:
        return 0.0
    return min(weighted / max_weight, 1.0)


def _bootstrap_ci(
    *,
    doc: dict,
    comp_b: float,
    comp_c: float,
    comp_d: float,
    comp_e: float,
    a_max: float,
    multiplier: float,
    acs: float,
) -> tuple[float, float]:
    """Bootstrap CI on the final ACS by resampling daily_buckets.

    Per §5.6: resample the 14-day daily counts with replacement, recompute
    component A per resample, hold B/C/D/E and the adjustment multiplier
    constant (they aggregate over the same window or are doc-level constants),
    then take the 2.5/97.5 percentile of the resulting ACS distribution.

    Falls back to a ±15% heuristic when there are fewer than 5 daily buckets
    (not enough samples for a meaningful resample).
    """
    buckets = doc.get("daily_buckets") or []
    counts_14d = [int(b.get("count", 0)) for b in buckets][-_WINDOW_14D:]

    if len(counts_14d) < _BOOTSTRAP_MIN_DAYS:
        return (
            max(0.0, acs * (1.0 - _HEURISTIC_CI_HALFWIDTH)),
            min(100.0, acs * (1.0 + _HEURISTIC_CI_HALFWIDTH)),
        )

    # Seed off the ticker so a re-score for the same doc is deterministic.
    ticker = doc.get("ticker", "")
    rng = np.random.default_rng(abs(hash(ticker)) % (2**32))
    arr = np.asarray(counts_14d, dtype=np.int64)
    n = len(arr)

    samples = np.empty(_BOOTSTRAP_N, dtype=np.float64)
    for i in range(_BOOTSTRAP_N):
        idx = rng.integers(0, n, size=n)
        resampled = arr[idx].tolist()
        dwd = _decay_weighted_density(resampled)
        comp_a_i = min(dwd, 1.0) * a_max
        acs_raw_i = comp_a_i + comp_b + comp_c + comp_d + comp_e
        samples[i] = min(100.0, max(0.0, acs_raw_i * multiplier))

    lower = float(np.percentile(samples, _BOOTSTRAP_LOWER_PCT))
    upper = float(np.percentile(samples, _BOOTSTRAP_UPPER_PCT))
    # Defensive rail: the CI must always bracket the point estimate.
    # In production the stored decay_weighted_density_14d and daily_buckets are
    # written by the same aggregator pass, so this clamp is a no-op. It only
    # bites if the two ever drift (e.g. test fixtures, partial replays).
    lower = min(lower, acs)
    upper = max(upper, acs)
    return max(0.0, lower), min(100.0, upper)
