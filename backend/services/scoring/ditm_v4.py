"""DITM v4 cross-sectional scorer (ADR-0032).

The v4 scorer differs from prior DITM scorers in three structural ways:

  1. **Cross-sectional, not per-row.** Each candidate's score depends on its
     factor rank within the scored set, not on absolute factor values.
     `score_universe` takes a list of candidates and returns a list of scores;
     a single candidate cannot be scored alone.

  2. **Rank-and-blend, not piecewise calibration.** Each of 13 factors is
     converted to a percentile rank 0..1, multiplied by a signed weight,
     summed, then percentile-mapped to 0..100. Missing factors are imputed
     to the median rank (0.5) so absent fundamentals contribute neutrally
     rather than as a free pass.

  3. **Honest signs.** Weights are calibrated against forward 120-day
     realised ROC on `ditm_backtest_pit.csv` (n=10,767, 2023-2026). Signs
     and magnitudes derive directly from the per-factor IC measured under
     point-in-time fundamentals. See ADR-0032 for the full table.

Public API:
  Candidate           — typed input row carrying the 13 factor values
  score_universe()    — score a list of candidates cross-sectionally
  FACTOR_DEFINITIONS  — canonical list of (name, sign, group, weight)
  GROUP_WEIGHT_CAPS   — per-group weight budgets that sum to 1.0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

__all__ = [
    "Candidate",
    "FACTOR_DEFINITIONS",
    "GROUP_WEIGHT_CAPS",
    "MIN_FACTORS_OBSERVED",
    "TIER_THRESHOLDS",
    "score_universe",
    "tier_for_score",
]


# ---------------------------------------------------------------------------
# Calibration constants (frozen by ADR-0032 — do not modify without an ADR)
# ---------------------------------------------------------------------------

# Per-group budgets. Within a group, individual factor weights are proportional
# to their |IC|; across groups, totals sum to ~1.0 of |w|.
GROUP_WEIGHT_CAPS: dict[str, float] = {
    "valuation": 0.35,
    "capital": 0.15,
    "technical": 0.20,
    "macro": 0.05,
    "option": 0.25,
}


# (factor_name, sign, group, raw_IC_from_backtest)
# Sign is encoded explicitly: +1 means "higher value → higher score",
# -1 means "lower value → higher score".
_RAW_FACTORS: tuple[tuple[str, int, str, float], ...] = (
    # Valuation — cheaper is better
    ("ps_ttm",         -1, "valuation", -0.1012),
    ("ev_sales",       -1, "valuation", -0.0897),
    ("ev_ebitda",      -1, "valuation", -0.0517),
    # Capital structure — modest leverage rewarded for DITM (vs CSP)
    ("debt_to_equity", +1, "capital",   +0.0764),
    ("nd_ebitda",      +1, "capital",   +0.0420),
    # Technicals — pullback in an uptrend
    ("wk_rsi",         -1, "technical", -0.0481),
    ("dist52w",        -1, "technical", -0.0272),
    ("hv30",           -1, "technical", -0.0292),
    ("ret_200d",       +1, "technical", +0.0214),
    # Macro
    ("sector_rs_6m",   -1, "macro",     -0.0439),
    # Option mechanics — preserves DITM identity
    ("leverage",       +1, "option",    +0.0288),
    ("delta",          +1, "option",    +0.0245),
    ("extrinsic_pct",  -1, "option",    -0.0234),
)


def _build_factor_table() -> list[tuple[str, int, str, float]]:
    """(name, sign, group, signed_weight). Weights sum to 1.0 of |w|."""
    by_group: dict[str, list[tuple[str, int, float]]] = {}
    for name, sign, group, ic in _RAW_FACTORS:
        by_group.setdefault(group, []).append((name, sign, ic))

    out: list[tuple[str, int, str, float]] = []
    for group, items in by_group.items():
        cap = GROUP_WEIGHT_CAPS[group]
        abs_sum = sum(abs(ic) for _, _, ic in items) or 1.0
        for name, sign, ic in items:
            weight = sign * cap * (abs(ic) / abs_sum)
            out.append((name, sign, group, weight))
    # Stable order: by group cap descending, then by |w| descending.
    out.sort(key=lambda t: (-GROUP_WEIGHT_CAPS[t[2]], -abs(t[3])))
    return out


FACTOR_DEFINITIONS: list[tuple[str, int, str, float]] = _build_factor_table()


# A candidate must have at least this many of the 13 factors observed
# (i.e. not None) to be assigned a score. Below this threshold the
# imputed-median contributions dominate too much of the score.
MIN_FACTORS_OBSERVED: int = 8


# Tier band thresholds, derived from backtest decile breakdown (ADR-0032).
# Score is a percentile [0, 100], so these are simple cuts.
TIER_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (90.0, "A"),
    (70.0, "B"),
    (50.0, "C"),
    (30.0, "D"),
    (0.0,  "E"),
)


# ---------------------------------------------------------------------------
# Candidate input
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """One input row for the cross-sectional scorer.

    `id` is opaque to the scorer; callers use it to map outputs back to
    application objects (DitmStrikeResult, etc.).

    `factors` carries the 13 v4 factor values keyed by name. Missing values
    must be present as None (do not omit the key). Unknown keys are ignored.
    """
    id: str
    factors: dict[str, float | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cross-sectional scoring
# ---------------------------------------------------------------------------

def _percentile_rank(values: list[float | None]) -> list[float | None]:
    """Average-rank → fraction in [0, 1]. None positions stay None.

    Implemented in pure Python (no numpy) so the scorer has no third-party
    dependencies and tests stay fast.
    """
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    n = len(indexed)
    if n == 0:
        return [None] * len(values)

    indexed.sort(key=lambda iv: iv[1])  # type: ignore[arg-type, return-value]
    out: list[float | None] = [None] * len(values)

    # Average-rank for ties: walk groups of equal values and assign each the
    # mean of the contiguous rank positions (1-based).
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-based mean of positions i..j
        pct = avg_rank / n          # in (0, 1]
        for k in range(i, j + 1):
            orig_idx = indexed[k][0]
            out[orig_idx] = pct
        i = j + 1
    return out


def _score_to_percentile(values: list[float]) -> list[float]:
    """Map raw weighted-rank sums to a 0..100 percentile distribution.

    Same average-rank treatment for ties; result is in (0, 100].
    """
    pct = _percentile_rank([v for v in values])  # type: ignore[list-item]
    return [round(p * 100, 2) if p is not None else 0.0 for p in pct]


@dataclass
class ScoreOutput:
    """Result row from `score_universe`."""
    id: str
    score: float | None  # None if the candidate had < MIN_FACTORS_OBSERVED
    tier: str | None     # 'A'..'E' or None
    n_observed: int      # how many of the 13 factors were not-None
    raw_weighted: float  # pre-percentile weighted rank sum (for diagnostics)


def score_universe(candidates: list[Candidate]) -> list[ScoreOutput]:
    """Score a universe of DITM candidates cross-sectionally.

    Steps:
      1. Per factor: percentile-rank the values across all candidates.
         (Missing values get rank=None, then imputed to 0.5 below.)
      2. Per candidate: sum signed_weight * rank for each factor, where
         missing factor ranks are imputed to 0.5 (cross-sectional median).
      3. Rows with < MIN_FACTORS_OBSERVED real factors get score=None.
      4. The remaining rows' raw weighted sums are percentile-mapped to
         a 0..100 score and assigned a tier per `TIER_THRESHOLDS`.

    Returns one `ScoreOutput` per input candidate, in the same order.
    Eligibility (n_observed) is reported even when score is None.
    """
    if not candidates:
        return []

    # Build per-factor value vectors aligned with the candidate list.
    factor_names = [f[0] for f in FACTOR_DEFINITIONS]
    n = len(candidates)
    per_factor_values: dict[str, list[float | None]] = {}
    for fname in factor_names:
        vals: list[float | None] = []
        for c in candidates:
            raw = c.factors.get(fname)
            if raw is None:
                vals.append(None)
            else:
                try:
                    fv = float(raw)
                    if fv != fv:  # NaN
                        vals.append(None)
                    else:
                        vals.append(fv)
                except (TypeError, ValueError):
                    vals.append(None)
        per_factor_values[fname] = vals

    # Compute per-factor percentile ranks.
    per_factor_ranks: dict[str, list[float | None]] = {
        fname: _percentile_rank(vals) for fname, vals in per_factor_values.items()
    }

    # Aggregate per-candidate weighted contributions.
    raw_scores: list[float] = [0.0] * n
    n_observed: list[int] = [0] * n
    for fname, _sign, _group, weight in FACTOR_DEFINITIONS:
        ranks = per_factor_ranks[fname]
        for i, r in enumerate(ranks):
            if r is None:
                # Median-impute: missing factor contributes weight * 0.5
                raw_scores[i] += weight * 0.5
            else:
                raw_scores[i] += weight * r
                n_observed[i] += 1

    # Eligibility mask: rows with too few observed factors get score=None.
    eligible_indices = [i for i in range(n) if n_observed[i] >= MIN_FACTORS_OBSERVED]

    # Percentile-map only the eligible raw scores into [0, 100].
    if eligible_indices:
        eligible_raw = [raw_scores[i] for i in eligible_indices]
        percentiles = _score_to_percentile(eligible_raw)
    else:
        percentiles = []
    final_scores: list[float | None] = [None] * n
    for k, idx in enumerate(eligible_indices):
        final_scores[idx] = percentiles[k]

    # Build outputs.
    out: list[ScoreOutput] = []
    for i, cand in enumerate(candidates):
        score = final_scores[i]
        tier = tier_for_score(score) if score is not None else None
        out.append(ScoreOutput(
            id=cand.id,
            score=score,
            tier=tier,
            n_observed=n_observed[i],
            raw_weighted=round(raw_scores[i], 6),
        ))
    return out


def tier_for_score(score: float) -> str:
    """Map a 0..100 score to its tier per `TIER_THRESHOLDS`."""
    for threshold, label in TIER_THRESHOLDS:
        if score >= threshold:
            return label
    return "E"  # unreachable: TIER_THRESHOLDS ends at 0.0
