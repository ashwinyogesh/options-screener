"""
Swing-trade composite scoring (v2.3.0).

Composite (raw) = R:R (40) + setup_score (30) + context (20) + institutional (10)

  R:R 40            : piecewise — 2.5→0, 3.0→25, 4.0→35, 5.0+→40
  setup 30          : best_setup score scaled to 0–30
  context 20        : ADX trend strength (10) + A/D line slope (10)
  institutional 10  : consecutive higher lows (5) + institutional ownership snapshot (5)

Cross-bucket multipliers (v2):

  final = raw × regime_factor × earnings_factor × extended_factor
        clamped to [0, 100]

  regime_factor    : 0.6–1.0, from services.swing.regime (composite-multiplier curve)
  earnings_factor  : 1.0 / 0.9 / 0.75 / 0.5 by days-to-earnings bucket
  extended_factor  : 0.7 if current price is >3% past structural trigger, else 1.0

The R:R *gate* (RR_HARD_GATE) is NOT here — it's set per-regime in
`services.swing.regime.RR_GATE_BY_REGIME` and enforced in the runner.

Hard gates handled by the runner BEFORE scoring:
  - R:R below the regime-specific gate
  - setup_score < 40
  - missing essentials (ATR, EMAs, ADV)
  - earnings within 1 day (any setup) or 7 days (reversion only)
  - reversion setup in risk_off regime
"""
from __future__ import annotations

SWING_SCORER_VERSION: str = "2.3.0"

SWING_WEIGHTS: dict[str, float] = {
    "RR": 40.0,
    "SETUP": 30.0,
    "CTX": 20.0,
    "INST": 10.0,
}
SWING_MAX: float = sum(SWING_WEIGHTS.values())  # 100.0


# --- Earnings multiplier bands (days to next earnings) -----------------------
EARNINGS_FACTOR_LE_3: float = 0.5
EARNINGS_FACTOR_LE_7: float = 0.75
EARNINGS_FACTOR_LE_14: float = 0.9

# --- Chasing penalty ---------------------------------------------------------
EXTENDED_FACTOR: float = 0.7


def _rr_points(rr: float) -> float:
    """Piecewise-linear R:R points."""
    if rr <= 2.5:
        return 0.0
    if rr >= 5.0:
        return 40.0
    if rr <= 3.0:
        # 2.5 → 0, 3.0 → 25
        return 25.0 * (rr - 2.5) / 0.5
    if rr <= 4.0:
        # 3.0 → 25, 4.0 → 35
        return 25.0 + 10.0 * (rr - 3.0)
    # 4.0 → 35, 5.0 → 40
    return 35.0 + 5.0 * (rr - 4.0)


def _setup_points(setup_score: float) -> float:
    """Setup score (0–100) → 0–30 points."""
    return max(0.0, min(30.0, setup_score * 0.30))


def _context_points(adx_value: float | None, ad_line_slope_pct: float | None) -> float:
    """ADX trend strength (10) + A/D line slope (10) = 20 pts.

    v2.2: replaces RS vs SPY + EMA alignment which were already scored inside
    the setup classifier (momentum setup). ADX and A/D slope appear in no
    setup detector's primary scoring, making this bucket genuinely orthogonal.

    ADX:       ≥30 → 10 · 22–30 → linear 7–10 · 15–22 → linear 3–7 · <15 → 0
    A/D slope: ≥5% → 10 · 0–5% → linear 0–10 · <0 → 0
    """
    adx_pts = 0.0
    if adx_value is not None and adx_value == adx_value:
        if adx_value >= 30.0:
            adx_pts = 10.0
        elif adx_value >= 22.0:
            adx_pts = 7.0 + (adx_value - 22.0) / 8.0 * 3.0
        elif adx_value >= 15.0:
            adx_pts = 3.0 + (adx_value - 15.0) / 7.0 * 4.0
    ad_pts = 0.0
    if ad_line_slope_pct is not None and ad_line_slope_pct == ad_line_slope_pct:
        if ad_line_slope_pct >= 5.0:
            ad_pts = 10.0
        elif ad_line_slope_pct > 0:
            ad_pts = 10.0 * ad_line_slope_pct / 5.0
    return adx_pts + ad_pts


def _institutional_points(
    higher_lows: int | None,
    institutional_ownership_pct: float | None,
) -> float:
    """Consecutive higher lows (5) + institutional ownership snapshot (5).

    v2.2: replaces A/D line slope (moved to context bucket at doubled weight)
    with higher_lows structure count. Higher lows appear in momentum setup
    scoring but not in breakout/reversion/retest, so the overlap is minor.

    Higher lows: ≥3 → 5 · 2 → 4 · 1 → 2 · 0 → 0
    Ownership:   ≥70% → 5 · 40–70% → linear · <40% → 0
    """
    hl_pts = 0.0
    if higher_lows is not None:
        if higher_lows >= 3:
            hl_pts = 5.0
        elif higher_lows == 2:
            hl_pts = 4.0
        elif higher_lows == 1:
            hl_pts = 2.0
    own_pts = 0.0
    if institutional_ownership_pct is not None and institutional_ownership_pct == institutional_ownership_pct:
        if institutional_ownership_pct >= 70:
            own_pts = 5.0
        elif institutional_ownership_pct >= 40:
            own_pts = 5.0 * (institutional_ownership_pct - 40) / 30.0
    return hl_pts + own_pts


def earnings_factor(days_to_earnings: int | None) -> float:
    """Multiplier based on proximity to next earnings report."""
    if days_to_earnings is None or days_to_earnings < 0:
        return 1.0
    if days_to_earnings <= 3:
        return EARNINGS_FACTOR_LE_3
    if days_to_earnings <= 7:
        return EARNINGS_FACTOR_LE_7
    if days_to_earnings <= 14:
        return EARNINGS_FACTOR_LE_14
    return 1.0


def compute_swing_score(
    rr: float,
    setup_score: float,
    adx_value: float | None,
    ad_line_slope_pct: float | None,
    higher_lows: int | None,
    institutional_ownership_pct: float | None,
    *,
    regime_factor: float = 1.0,
    days_to_earnings: int | None = None,
    extended: bool = False,
) -> dict:
    """
    Returns:
      score        : float 0–100 (post-multipliers)
      raw_score    : float 0–100 (pre-multipliers)
      breakdown    : dict of factor → points (raw, pre-multipliers)
      multipliers  : dict regime/earnings/extended → factor used
      confidence   : "high" | "medium" | "speculative"

    v2.2 context bucket: ADX trend strength (10) + A/D slope (10).
    v2.2 institutional bucket: higher_lows (5) + ownership snapshot (5).

    Confidence tiers reference the POST-multiplier score and rr.
    With ATR-projection targets (v2.2), R:R varies per symbol so
    the high tier (rr ≥ 3.5) is now reachable on tight setups.
    """
    rr_pts = round(_rr_points(rr), 2)
    setup_pts = round(_setup_points(setup_score), 2)
    ctx_pts = round(_context_points(adx_value, ad_line_slope_pct), 2)
    inst_pts = round(_institutional_points(higher_lows, institutional_ownership_pct), 2)
    raw = round(rr_pts + setup_pts + ctx_pts + inst_pts, 2)

    e_factor = earnings_factor(days_to_earnings)
    x_factor = EXTENDED_FACTOR if extended else 1.0
    final = raw * regime_factor * e_factor * x_factor
    final = round(max(0.0, min(100.0, final)), 2)

    if final >= 75 and rr >= 3.5 and setup_score >= 70:
        confidence = "high"
    elif final >= 55:
        confidence = "medium"
    else:
        confidence = "speculative"

    return {
        "score": final,
        "raw_score": raw,
        "breakdown": {
            "rr": rr_pts,
            "setup": setup_pts,
            "context": ctx_pts,
            "institutional": inst_pts,
        },
        "multipliers": {
            "regime": round(regime_factor, 3),
            "earnings": round(e_factor, 3),
            "extended": round(x_factor, 3),
        },
        "confidence": confidence,
    }
