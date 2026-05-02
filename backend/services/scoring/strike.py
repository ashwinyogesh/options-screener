"""
Strike-quality scorers + final-blend helpers for the CSP and CC screeners.

Both scorers share the same factor structure (Δ, distance to S/R, EM, OTM, BA,
LQ, ROC) but with direction-specific math: CSP wants short puts below strong
support, CC wants short calls below stiff resistance.

DITM strike scoring is intentionally *not* in this module yet — the live
implementation lives inline in `services.ditm_service.py` (its factor mix
is fundamentally different: extrinsic % matters more than EM, no S/R
distance factor). Phase 4 of the screener refactor will migrate it here
once `ScreenerConfig` exists (see ADR-0002).
"""
from __future__ import annotations

import math

__all__ = [
    "compute_csp_strike_score",
    "compute_csp_final_score",
    "compute_cc_strike_score",
    "compute_cc_final_score",
]


def compute_csp_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    iv_used: float,
    dte: int,
    vol_support_1: float | None,
    vol_support_2: float | None,
    vol_support_3: float | None,
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
    credit: float | None = None,   # per-share premium for ROC factor
) -> tuple[float, str, dict]:
    """
    CSP Strike Safety Score 0–100.

    Weights: Δ 15 + Sup 18 + EM 20 + OTM 9 + BA 23 + LQ 5 + ROC 10 = 100
    """
    score = 0.0
    bk: dict[str, float] = {}

    # --- Delta bell-curve (15 pts — rescaled from 18 by ×15/18) ---
    p = 0.0
    if not math.isnan(delta):
        if -0.25 <= delta <= -0.20:
            p = 15.0
        elif (-0.30 <= delta < -0.25) or (-0.20 < delta <= -0.15):
            p = 10.0
        elif -0.15 < delta <= -0.10:
            p = 5.0
        elif delta < -0.30:
            p = 5.833
    score += p; bk['Δ'] = p

    # --- Distance vs Nearest Support Below Strike (18 pts) — unchanged ---
    p = 0.0
    _csp_dist_pct: float | None = None
    supports = [s for s in [vol_support_1, vol_support_2, vol_support_3] if s is not None]
    supports_below = [s for s in supports if s < strike]
    if supports_below:
        nearest = max(supports_below)
        gap_pct = (strike - nearest) / strike * 100.0
        _csp_dist_pct = round(gap_pct, 2)
        if gap_pct <= 0:
            p = 18.0
        elif gap_pct <= 5:
            p = 18.0 - gap_pct / 5.0 * 8.0
        elif gap_pct <= 10:
            p = 10.0 - (gap_pct - 5) / 5.0 * 10.0
    elif supports:
        p = 7.0
    score += p; bk['Sup'] = p

    # --- Expected Move Buffer (20 pts) — recalibrated reference to 0.5× EM ---
    # Prior formula used 1×EM as the lower boundary, making sigmas_outside ≈ -0.25
    # at the target delta (-0.225), earning 0 pts always. Using 0.5×EM means a
    # -0.225 delta put is ~0.25 EM units outside the new boundary → full 20 pts.
    p = 0.0
    _em_buffer_pct: float = float('nan')
    if not math.isnan(iv_used) and iv_used > 0 and dte > 0:
        T = dte / 365.0
        em = current_price * iv_used * math.sqrt(T)
        em_half_lower = current_price - 0.5 * em   # 0.5× EM reference boundary
        sigmas_outside = (em_half_lower - strike) / em
        _em_buffer_pct = round(sigmas_outside * 100, 2)
        if sigmas_outside >= 0.20:
            p = 20.0
        elif sigmas_outside >= 0.0:
            p = 13.0 + sigmas_outside / 0.20 * 7.0
        elif sigmas_outside >= -0.10:
            p = 5.0 + (sigmas_outside + 0.10) / 0.10 * 8.0
    score += p; bk['EM'] = p

    # --- % OTM from Spot (9 pts — rescaled from 12 by ×0.75) ---
    p = 0.0
    otm_pct = (current_price - strike) / current_price * 100.0
    if otm_pct >= 15:
        p = 9.0
    elif otm_pct >= 10:
        p = 6.75 + (otm_pct - 10) / 5.0 * 2.25
    elif otm_pct >= 5:
        p = 4.5 + (otm_pct - 5) / 5.0 * 2.25
    elif otm_pct >= 2:
        p = 1.5 + (otm_pct - 2) / 3.0 * 3.0
    score += p; bk['OTM'] = p

    # --- Bid-Ask Spread % (23 pts — rescaled from 27 by ×23/27) ---
    p = 0.0
    if bid_ask_spread_pct is not None and not math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 1.0:
            p = 23.0
        elif bid_ask_spread_pct <= 3.0:
            p = 15.333 + (3.0 - bid_ask_spread_pct) / 2.0 * 7.667
        elif bid_ask_spread_pct <= 5.0:
            p = 8.519 + (5.0 - bid_ask_spread_pct) / 2.0 * 6.815
        elif bid_ask_spread_pct <= 8.0:
            p = 2.130 + (8.0 - bid_ask_spread_pct) / 3.0 * 6.389
    score += p; bk['BA'] = p

    # --- OI / Volume at this strike (5 pts) — unchanged ---
    p = 0.0
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 1000:
        p = 5.0
    elif liquidity_count >= 500:
        p = 3.5 + (liquidity_count - 500) / 500.0 * 1.5
    elif liquidity_count >= 200:
        p = 2.0 + (liquidity_count - 200) / 300.0 * 1.5
    elif liquidity_count >= 100:
        p = (liquidity_count - 100) / 100.0 * 2.0
    score += p; bk['LQ'] = p

    # --- Annualized ROC (10 pts) — recalibrated: full credit threshold 30% → 20% ---
    # Prior 30% threshold was only achievable on illiquid chains that simultaneously
    # fail Bid-Ask. Lowered to 20% so NVDA/AMD/AMAT at delta -0.225 earn near-full pts.
    # CSP capital = (strike * 100) − credit*100 (cash secured minus premium received)
    # Per-share: capital = strike − credit
    p = 0.0
    _roc_annualized: float = float('nan')
    if credit is not None and credit > 0 and dte > 0:
        capital_per_share = strike - credit
        if capital_per_share > 0:
            roc = (credit / capital_per_share) * (365.0 / dte) * 100.0
            _roc_annualized = round(roc, 2)
            if roc >= 20:
                p = 10.0
            elif roc >= 14:
                p = 7.0 + (roc - 14) / 6.0 * 3.0     # 7 → 10
            elif roc >= 8:
                p = 4.0 + (roc - 8) / 6.0 * 3.0      # 4 → 7
            elif roc >= 4:
                p = 1.0 + (roc - 4) / 4.0 * 3.0      # 1 → 4
    score += p; bk['ROC'] = p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    raw = {
        'dist_pct': _csp_dist_pct,
        'em_buffer_pct': _em_buffer_pct,
        'otm_pct': otm_pct,
        'lq_count': liquidity_count,
        'roc_annualized': _roc_annualized,
    }
    return round(max(0.0, min(100.0, score)), 1), detail, raw


def compute_csp_final_score(env_score: float, strike_score: float) -> float:
    """Final Score = 0.4 × Env Score + 0.6 × Strike Score."""
    return round(0.4 * env_score + 0.6 * strike_score, 1)


def compute_cc_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    iv_used: float,
    dte: int,
    vol_resistance_1: float | None,
    vol_resistance_2: float | None,
    vol_resistance_3: float | None,
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
    credit: float | None = None,   # per-share premium for ROC factor
) -> tuple[float, str, dict]:
    """
    CC Strike Safety Score 0–100.

    Weights: Δ 15 + Res 18 + EM 20 + OTM 9 + BA 23 + LQ 5 + ROC 10 = 100
    """
    score = 0.0
    bk: dict[str, float] = {}

    # --- Delta bell-curve (15 pts — rescaled from 18 by ×15/18) ---
    p = 0.0
    if not math.isnan(delta):
        if 0.20 <= delta <= 0.25:
            p = 15.0
        elif (0.15 <= delta < 0.20) or (0.25 < delta <= 0.30):
            p = 10.0
        elif 0.10 <= delta < 0.15:
            p = 5.0
        elif delta > 0.30:
            p = 5.833
    score += p; bk['Δ'] = p

    # --- Distance vs Nearest Resistance Above Current Price (18 pts) — unchanged ---
    p = 0.0
    _cc_dist_pct: float | None = None
    resistances = [r for r in [vol_resistance_1, vol_resistance_2, vol_resistance_3] if r is not None]
    resistances_above_price = [r for r in resistances if r > current_price]
    if resistances_above_price:
        nearest_R = min(resistances_above_price)
        gap_pct = (nearest_R - strike) / strike * 100.0
        _cc_dist_pct = round(gap_pct, 2)
        if gap_pct <= -20:
            p = 3.0
        elif gap_pct <= -10:
            p = 3.0 + (gap_pct + 20.0) / 10.0 * 15.0
        elif gap_pct <= 0:
            p = 18.0
            if all(r <= strike for r in resistances_above_price):
                p += 5.0
        elif gap_pct <= 5:
            p = 18.0 - gap_pct / 5.0 * 8.0
        elif gap_pct <= 10:
            p = 10.0 - (gap_pct - 5) / 5.0 * 10.0
    score += p; bk['Res'] = p

    # --- Expected Move Buffer (20 pts) — recalibrated reference to 0.5× EM ---
    # Same fix as CSP: uses 0.5×EM as the boundary so +0.225 delta calls earn pts.
    p = 0.0
    _cc_em_buffer_pct: float = float('nan')
    if not math.isnan(iv_used) and iv_used > 0 and dte > 0:
        T = dte / 365.0
        em = current_price * iv_used * math.sqrt(T)
        em_half_upper = current_price + 0.5 * em   # 0.5× EM reference boundary
        sigmas_outside = (strike - em_half_upper) / em
        _cc_em_buffer_pct = round(sigmas_outside * 100, 2)
        if sigmas_outside >= 0.20:
            p = 20.0
        elif sigmas_outside >= 0.0:
            p = 13.0 + sigmas_outside / 0.20 * 7.0
        elif sigmas_outside >= -0.10:
            p = 5.0 + (sigmas_outside + 0.10) / 0.10 * 8.0
    score += p; bk['EM'] = p

    # --- % OTM from Spot (9 pts — rescaled from 12 by ×0.75) ---
    p = 0.0
    otm_pct = (strike - current_price) / current_price * 100.0
    if otm_pct >= 15:
        p = 9.0
    elif otm_pct >= 10:
        p = 6.75 + (otm_pct - 10) / 5.0 * 2.25
    elif otm_pct >= 5:
        p = 4.5 + (otm_pct - 5) / 5.0 * 2.25
    elif otm_pct >= 2:
        p = 1.5 + (otm_pct - 2) / 3.0 * 3.0
    score += p; bk['OTM'] = p

    # --- Bid-Ask Spread % (23 pts — rescaled from 27 by ×23/27) ---
    p = 0.0
    if bid_ask_spread_pct is not None and not math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 1.0:
            p = 23.0
        elif bid_ask_spread_pct <= 3.0:
            p = 15.333 + (3.0 - bid_ask_spread_pct) / 2.0 * 7.667
        elif bid_ask_spread_pct <= 5.0:
            p = 8.519 + (5.0 - bid_ask_spread_pct) / 2.0 * 6.815
        elif bid_ask_spread_pct <= 8.0:
            p = 2.130 + (8.0 - bid_ask_spread_pct) / 3.0 * 6.389
    score += p; bk['BA'] = p

    # --- OI / Volume at this strike (5 pts) — unchanged ---
    p = 0.0
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 1000:
        p = 5.0
    elif liquidity_count >= 500:
        p = 3.5 + (liquidity_count - 500) / 500.0 * 1.5
    elif liquidity_count >= 200:
        p = 2.0 + (liquidity_count - 200) / 300.0 * 1.5
    elif liquidity_count >= 100:
        p = (liquidity_count - 100) / 100.0 * 2.0
    score += p; bk['LQ'] = p

    # --- Annualized ROC (10 pts) — recalibrated: full credit threshold 30% → 20% ---
    # CC capital basis = current price (not cost basis — out of scope)
    # Per-share: capital = current_price − credit
    p = 0.0
    _roc_annualized: float = float('nan')
    if credit is not None and credit > 0 and dte > 0 and current_price > 0:
        capital_per_share = current_price - credit
        if capital_per_share > 0:
            roc = (credit / capital_per_share) * (365.0 / dte) * 100.0
            _roc_annualized = round(roc, 2)
            if roc >= 20:
                p = 10.0
            elif roc >= 14:
                p = 7.0 + (roc - 14) / 6.0 * 3.0
            elif roc >= 8:
                p = 4.0 + (roc - 8) / 6.0 * 3.0
            elif roc >= 4:
                p = 1.0 + (roc - 4) / 4.0 * 3.0
    score += p; bk['ROC'] = p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    raw = {
        'dist_pct': _cc_dist_pct,
        'em_buffer_pct': _cc_em_buffer_pct,
        'otm_pct': otm_pct,
        'lq_count': liquidity_count,
        'roc_annualized': _roc_annualized,
    }
    return round(max(0.0, min(100.0, score)), 1), detail, raw


def compute_cc_final_score(env_score: float, strike_score: float) -> float:
    """CC Final Score = 0.4 × Env Score + 0.6 × Strike Score."""
    return round(0.4 * env_score + 0.6 * strike_score, 1)
