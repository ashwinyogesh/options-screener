"""
Swing-trade composite scoring (v3.0.0).

Composite (raw) = R:R (40) + setup (30) + MACD momentum (25) + BB position (20) + volume surge (10) = 125 max → clamped to 100

  v3.0 redesign (2026-05, IC-driven):
  Replaced the broken context (ADX + A/D slope, IC ≈ 0) and institutional
  (higher_lows + ownership, IC < 0) buckets with IC-validated signals from a
  3,366-trade walk-forward backtest (2024-01 → 2026-05):

    Factor           old IC       new IC vs r_realized
    R:R              +0.213       kept; curve steepened to use full 2.5–3.0 range
    setup_score      +0.040       kept (positive, stable)
    MACD histogram   +0.209       replaces ADX+A/D (IC ≈ 0)   → 25 pts
    BB position      +0.180       replaces higher_lows+inst_own (IC < 0) → 20 pts
    volume surge     +0.125       new bucket  → 10 pts

  Regime multiplier removed: IC = −0.082 in the backtest; regime is already
  captured by the rr_gate (higher gate in risk_off ⟹ fewer but better trades).

  Extended penalty removed: IC = −0.228 — extended/chasing setups outperformed
  non-extended in the 2024-2026 bull-market sample (likely momentum continuation).
  `extended` is retained as a display flag only; no scoring impact.

  Earnings multiplier retained: rational binary-event risk haircut.

  Overall IC:
    v3.0 rho(score, r_realized) = +0.250  (n=3,366)  ← new
    v2.4 rho(score, r_realized) = −0.016              ← old

  Win rates by tier (v3.0 backtest):
    80+   → 62.6% win, median R +2.74
    65–79 → 52.1% win, median R +1.29
    50–64 → 46.9% win
    35–49 → 44.0% win
    < 35  → 31.8% win

The R:R gate (regime-dependent 2.5/2.75/3.0) is NOT here — it's enforced
upstream in the runner.
"""
from __future__ import annotations

SWING_SCORER_VERSION: str = "3.0.0"

# Component maximums (sum = 125; raw is clamped to 100).
SWING_WEIGHTS: dict[str, float] = {
    "RR":    40.0,
    "SETUP": 30.0,
    "MACD":  25.0,   # MACD histogram (replaces ADX + A/D slope)
    "BB":    20.0,   # Bollinger Band position (replaces higher_lows + ownership)
    "VOL":   10.0,   # volume / 20-day average surge ratio
}
SWING_MAX_RAW: float = sum(SWING_WEIGHTS.values())  # 125 → clamped to 100

# Earnings multiplier bands (unchanged)
EARNINGS_FACTOR_LE_3: float = 0.5
EARNINGS_FACTOR_LE_7: float = 0.75
EARNINGS_FACTOR_LE_14: float = 0.9


def _rr_points(rr: float) -> float:
    """Steepened piecewise: uses the full 40-pt range over the actual 2.5–3.0 domain.

    In the 3,366-trade backtest all trades had rr ∈ [2.5, 3.0] (ATR-based targets
    cap realistic R:R at ~3.0). The v2.x curve gave ≤ 25 pts at rr=3.0; v3.0
    now assigns the full 40 pts at rr=3.0 to use the bandwidth.
    """
    if rr <= 2.5:
        return 0.0
    if rr >= 3.0:
        return 40.0
    # linear 2.5 → 0, 3.0 → 40
    return round(40.0 * (rr - 2.5) / 0.5, 2)


def _setup_points(setup_score: float) -> float:
    """Setup quality (0–100) → 0–30 pts. Unchanged from v2.x."""
    return round(max(0.0, min(30.0, setup_score * 0.30)), 2)


def _macd_points(macd_hist_val: float | None) -> float:
    """MACD histogram → 0–25 pts.

    Positive histogram = MACD above signal line = bullish momentum.
    rho(macd_hist, r_realized) = +0.209 (n=3,366 backtest).

    Thresholds tuned to the backtest distribution:
      p50 = −0.05, p75 = +0.22, p90 ≈ +0.80.
    """
    if macd_hist_val is None or macd_hist_val != macd_hist_val:
        return 0.0
    m = macd_hist_val
    if m >= 0.5:
        return 25.0
    if m >= 0.1:
        # 0.1 → 15, 0.5 → 25
        return round(15.0 + (m - 0.1) / 0.4 * 10.0, 2)
    if m >= 0.0:
        # 0 → 0, 0.1 → 15 (steep ramp in the near-zero zone)
        return round(8.0 * m / 0.1, 2)
    return 0.0  # negative histogram → no credit


def _bb_points(bb_position: float | None) -> float:
    """Bollinger Band position → 0–20 pts.

    0 = at lower band, 1 = at upper band. Higher is better (momentum signal).
    rho(bb_pos, r_realized) = +0.180 (n=3,366 backtest).

    Backtest distribution: p50=0.21, p75=0.42, p90≈0.65.
    """
    if bb_position is None or bb_position != bb_position:
        return 0.0
    b = bb_position
    if b >= 0.7:
        return 20.0
    if b >= 0.5:
        return round(12.0 + (b - 0.5) / 0.2 * 8.0, 2)
    if b >= 0.3:
        return round(4.0 + (b - 0.3) / 0.2 * 8.0, 2)
    if b >= 0.0:
        return round(max(0.0, b * 13.0), 2)
    return 0.0  # below lower band → no credit


def _vol_points(vol_surge_ratio: float | None) -> float:
    """Volume vs 20-day average → 0–10 pts.

    rho(vol_surge_20, r_realized) = +0.125 (n=3,366 backtest).
    Backtest distribution: mean=1.16, p75=1.22, p90≈2.0.
    """
    if vol_surge_ratio is None or vol_surge_ratio != vol_surge_ratio:
        return 0.0
    v = vol_surge_ratio
    if v >= 2.0:
        return 10.0
    if v >= 1.5:
        return round(7.0 + (v - 1.5) / 0.5 * 3.0, 2)
    if v >= 1.2:
        return round(4.0 + (v - 1.2) / 0.3 * 3.0, 2)
    return 0.0


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
    macd_hist_val: float | None,
    bb_position: float | None,
    vol_surge_ratio: float | None,
    *,
    days_to_earnings: int | None = None,
    extended: bool = False,
) -> dict:
    """
    Compute the v3.0 composite swing score.

    Returns:
      score        : float 0–100 (post earnings multiplier)
      raw_score    : float 0–100 (pre multiplier; clamped from 0–125 sum)
      breakdown    : {rr, setup, macd, bb, vol} — component pts
      multipliers  : {earnings} — only active multiplier
      confidence   : "high" | "medium" | "speculative"

    `extended` is accepted for call-site compatibility and surfaced in
    SwingResult but does NOT modify the score in v3.0 (IC = −0.228; the
    penalty was wrong-signed in the 2024-2026 backtest).
    """
    rr_pts  = _rr_points(rr)
    stp_pts = _setup_points(setup_score)
    mcd_pts = _macd_points(macd_hist_val)
    bb_pts  = _bb_points(bb_position)
    vol_pts = _vol_points(vol_surge_ratio)

    raw = round(min(100.0, rr_pts + stp_pts + mcd_pts + bb_pts + vol_pts), 2)

    e_factor = earnings_factor(days_to_earnings)
    final = round(max(0.0, min(100.0, raw * e_factor)), 2)

    # v3.0 confidence tiers calibrated to empirical distribution:
    # P90≈77, P75≈65, P50≈46, P25≈34.
    # Win rates: 80+=62.6%, 65-79=52.1%, 50-64=46.9%, 35-49=44%, <35=31.8%.
    if final >= 75 and setup_score >= 60:
        confidence = "high"
    elif final >= 50:
        confidence = "medium"
    else:
        confidence = "speculative"

    return {
        "score": final,
        "raw_score": raw,
        "breakdown": {
            "rr":    rr_pts,
            "setup": stp_pts,
            "macd":  mcd_pts,
            "bb":    bb_pts,
            "vol":   vol_pts,
        },
        "multipliers": {
            "earnings": round(e_factor, 3),
        },
        "confidence": confidence,
    }
