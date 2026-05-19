"""ACS computation — pure functions (Phase 6).

Implements NARRATIVE_METHODOLOGY.md §5.

Components:
    A  Attention persistence  — decay_weighted_density_14d * A_max
    B  Contributor quality   — unique_authors / log(mentions) * (1-G) * B_max
    C  Narrative strength    — stage_map[stage] * stage_confidence * (C_max / 20)
    D  Thesis quality        — min(0.6*s_br + 0.2*s_Br, 1) * D_max   (ADR-0021)
    E  Market confirmation   — 0 (deferred to Phase 6.1)

Adjustments (multipliers, in order — §5.3):
    G > 0.65                                → × 0.6   (gini_high)
    3 consecutive days of decreasing mentions → × 0.8 (decelerating_3d)
    lifecycle_stage > 3                     → × 0.5   (late_stage)
    0 < market_cap < $100M                  → × 0.85  (small_cap)

CI bands: bootstrap percentile (resampling daily_buckets) when ≥5 days are
available; otherwise fall back to a ±15% heuristic.
Time decay: ACS(t) = ACS_raw * e^{-0.07 * t} where t = days since acs_scored_at.

Alerts (Phase 7 — detect_alerts):
    stage_2_entry   — ticker just entered stage 2 (entry window opening)
    stage_3_entry   — ticker just entered stage 3 (growing, peak score window)
    acs_rising_fast — ACS jumped ≥ 15 points vs prior day
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

    # --- Component D: thesis quality (ADR-0021) ---
    # s_br = P(direction=bull ∧ substance=researched) over classified 14d signals.
    # s_Br = P(direction=bear ∧ substance=researched). Both are joint shares
    # written by the aggregator (compute_axis_distributions); they cannot be
    # derived from the marginal axis ratios. Component D rewards substantive
    # conviction in either direction — a deeply argued bear case still counts.
    s_br: float = doc.get("conviction_bull_researched_share") or 0.0
    s_Br: float = doc.get("conviction_bear_researched_share") or 0.0
    thesis_score = (0.6 * s_br) + (0.2 * s_Br)
    comp_d = min(max(thesis_score, 0.0), 1.0) * d_max

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
    """Return the compound axis label (direction×substance) for this doc.

    ADR-0021 — derived from axis marginals only. Returns one of
    ``bull_researched`` / ``bull_emotional`` / ``bear_researched`` /
    ``bear_emotional``. Falls back to the raw sentiment polarity
    (``bullish`` / ``bearish``) when no axis data is available, and
    ``unknown`` when nothing has been classified at all.
    """
    bull = doc.get("conviction_bull_share")
    researched = doc.get("conviction_researched_share")
    if bull is not None and researched is not None:
        direction = "bull" if bull >= 0.5 else "bear"
        substance = "researched" if researched >= 0.5 else "emotional"
        return f"{direction}_{substance}"

    bullish: float = doc.get("bullish_ratio") or 0.0
    bearish: float = doc.get("bearish_ratio") or 0.0
    if bullish > 0 or bearish > 0:
        return "bullish" if bullish >= bearish else "bearish"
    return "unknown"


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


# ---------------------------------------------------------------------------
# ADR-0023 — Emerging-tab continuity fields
# ---------------------------------------------------------------------------
# Computed once per ticker per scorer run from prior-day ticker_timeline docs
# fetched via ScorerCosmosClient.fetch_history. Pure function so it is fully
# unit-testable without Cosmos.
# ---------------------------------------------------------------------------

# Stage values that count as "emerging" (target entry window §4 / §14).
_EMERGING_STAGES: frozenset[int] = frozenset({1, 2, 3})
# Minimum prior-day samples for a stable OLS slope. Matches the bootstrap
# floor used by _bootstrap_ci; below this we return None rather than show
# a misleading trend on a brand-new ticker.
_SLOPE_MIN_SAMPLES: int = 5
# Slope is computed over today + up to 13 prior days.
_SLOPE_WINDOW_DAYS: int = 14


@dataclass(frozen=True)
class ContinuityFields:
    """ADR-0023 continuity scalars written to today's ticker_timeline doc."""
    stage_streak_days: int
    first_emerged_at: str | None
    acs_slope_14d: float | None


def compute_continuity_fields(
    today_stage: int | None,
    today_bucket_date: str,
    today_acs: float,
    history: list[dict],
) -> ContinuityFields:
    """Derive ADR-0023 continuity fields from today's values plus prior docs.

    Args:
        today_stage:       lifecycle_stage on today's doc (may be None when the
                           detector has not yet run on today's bucket).
        today_bucket_date: ISO date string of today's bucket.
        today_acs:         today's freshly-computed ACS (already haircut-applied).
        history:           prior ticker_timeline docs for this ticker, newest
                           first. Each dict has ``bucket_date``,
                           ``lifecycle_stage`` (int | None), and ``acs``
                           (float | None). See ScorerCosmosClient.fetch_history.

    Returns:
        ContinuityFields. ``stage_streak_days`` counts consecutive emerging-stage
        days ending today, treating today's None as carry-forward from the most
        recent prior non-null stage (24h carry-forward per ADR-0023).
        ``first_emerged_at`` is the bucket_date of the oldest day in the streak,
        or None when the streak is zero. ``acs_slope_14d`` is the OLS slope of
        ACS against day index over up to 14 daily samples (today + prior),
        skipping any day where ACS is None. Returns None for slope when fewer
        than 5 valid samples are available.
    """
    streak, first_emerged = _streak_and_first_emerged(
        today_stage=today_stage,
        today_bucket_date=today_bucket_date,
        history=history,
    )
    slope = _acs_slope(today_acs=today_acs, history=history)
    return ContinuityFields(
        stage_streak_days=streak,
        first_emerged_at=first_emerged,
        acs_slope_14d=slope,
    )


def _streak_and_first_emerged(
    *,
    today_stage: int | None,
    today_bucket_date: str,
    history: list[dict],
) -> tuple[int, str | None]:
    """Walk backward from today, counting consecutive emerging-stage days.

    Carry-forward rule (ADR-0023): a single None at the most recent end of the
    window is treated as the prior day's stage, so a render in the morning
    before the detector has run does not zero a long streak. Multiple consecutive
    Nones break the streak — we deliberately limit carry-forward to "today only"
    because the detector runs hourly.
    """
    # Effective today's stage with one-step carry-forward from history.
    effective_today = today_stage
    if effective_today is None:
        for prior in history:
            ps = prior.get("lifecycle_stage")
            if ps is not None:
                effective_today = int(ps)
                break

    if effective_today not in _EMERGING_STAGES:
        return 0, None

    # Today counts as day 1; walk back through history while the stage stays
    # in the emerging set. Treat None inside the walk as a break (we only
    # carry-forward at the leading edge, never mid-streak).
    streak = 1
    first_date = today_bucket_date
    for prior in history:
        ps = prior.get("lifecycle_stage")
        if ps is None or int(ps) not in _EMERGING_STAGES:
            break
        streak += 1
        first_date = str(prior.get("bucket_date") or first_date)

    return streak, first_date


def _acs_slope(*, today_acs: float, history: list[dict]) -> float | None:
    """OLS slope of ACS against day index over today + up to 13 prior days.

    Day index is days-ago (today = 0, yesterday = 1, ...). Slope is returned
    in "ACS points per day moving forward in time" — so a positive slope means
    ACS is rising. None when fewer than _SLOPE_MIN_SAMPLES valid (non-None ACS)
    samples are available.
    """
    # Build (days_ago, acs) pairs, today first.
    samples: list[tuple[int, float]] = [(0, float(today_acs))]
    for i, prior in enumerate(history[: _SLOPE_WINDOW_DAYS - 1], start=1):
        acs_val = prior.get("acs")
        if acs_val is None:
            continue
        samples.append((i, float(acs_val)))

    if len(samples) < _SLOPE_MIN_SAMPLES:
        return None

    # Convert "days_ago" to "day_index moving forward" so a positive slope is
    # a rising trend. day_index = -days_ago is equivalent up to sign.
    xs = np.asarray([-x for x, _ in samples], dtype=np.float64)
    ys = np.asarray([y for _, y in samples], dtype=np.float64)
    # np.polyfit returns [slope, intercept] for deg=1.
    slope, _intercept = np.polyfit(xs, ys, 1)
    return float(slope)


# ---------------------------------------------------------------------------
# Alert detection (Phase 7)
# ---------------------------------------------------------------------------

_ACS_SPIKE_THRESHOLD: float = 15.0   # ACS points/day — roughly 15% of max scale


def detect_alerts(
    ticker: str,
    today_stage: int | None,
    today_acs: float,
    bucket_date: str,
    history: list[dict],
) -> list[dict]:
    """Return alert dicts for any threshold conditions fired this scoring run.

    Each dict is ready to upsert into the Cosmos ``alerts`` container.
    Idempotent: ``id`` is ``{ticker}_{alert_type}_{bucket_date}`` so
    re-scoring the same day does not duplicate alerts.

    Args:
        ticker:       Symbol being scored.
        today_stage:  lifecycle_stage on today's doc (may be None).
        today_acs:    Freshly-computed ACS value.
        bucket_date:  ISO date string of today's bucket (e.g. "2026-05-19").
        history:      Prior ticker_timeline docs, newest first (from fetch_history).
    """
    alerts: list[dict] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    prior = history[0] if history else None
    prior_stage = int(prior.get("lifecycle_stage") or 0) if prior else None
    prior_acs = float(prior.get("acs") or 0.0) if prior else None

    # stage_2_entry — just entered the opening of the entry window.
    if today_stage == 2 and prior_stage != 2:
        alerts.append({
            "id": f"{ticker}_stage_2_entry_{bucket_date}",
            "ticker": ticker,
            "alert_type": "stage_2_entry",
            "triggered_at": now_iso,
            "bucket_date": bucket_date,
            "payload": {
                "prev_stage": prior_stage,
                "curr_stage": 2,
                "acs": round(today_acs, 1),
            },
        })

    # stage_3_entry — thesis growing, peak score window, premium may be elevated.
    if today_stage == 3 and prior_stage != 3:
        alerts.append({
            "id": f"{ticker}_stage_3_entry_{bucket_date}",
            "ticker": ticker,
            "alert_type": "stage_3_entry",
            "triggered_at": now_iso,
            "bucket_date": bucket_date,
            "payload": {
                "prev_stage": prior_stage,
                "curr_stage": 3,
                "acs": round(today_acs, 1),
            },
        })

    # acs_rising_fast — significant ACS jump vs prior day.
    if prior_acs is not None and today_acs - prior_acs >= _ACS_SPIKE_THRESHOLD:
        alerts.append({
            "id": f"{ticker}_acs_rising_fast_{bucket_date}",
            "ticker": ticker,
            "alert_type": "acs_rising_fast",
            "triggered_at": now_iso,
            "bucket_date": bucket_date,
            "payload": {
                "prev_acs": round(prior_acs, 1),
                "curr_acs": round(today_acs, 1),
                "delta": round(today_acs - prior_acs, 1),
                "stage": today_stage,
            },
        })

    return alerts
