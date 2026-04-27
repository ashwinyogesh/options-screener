"""
Strike-quality scorers + final-blend helpers.

CSP and CC scorers share the same factor structure (Δ, distance to S/R, EM,
OTM, BA, LQ, ROC) but with direction-specific math. DITM is a different beast
(extrinsic % matters more than EM, no S/R distance factor).
"""
from __future__ import annotations


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
    import math as _math
    score = 0.0
    bk: dict[str, float] = {}

    # --- Delta bell-curve (15 pts — rescaled from 18 by ×15/18) ---
    p = 0.0
    if not _math.isnan(delta):
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

    # --- Expected Move Buffer (20 pts) — unchanged ---
    p = 0.0
    _em_buffer_pct: float = float('nan')
    if not _math.isnan(iv_used) and iv_used > 0 and dte > 0:
        T = dte / 365.0
        em = current_price * iv_used * _math.sqrt(T)
        em_lower = current_price - em
        sigmas_outside = (em_lower - strike) / em
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
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
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

    # --- Annualized ROC (10 pts) — provisional curve, calibrate empirically ---
    # CSP capital = (strike * 100) − credit*100 (cash secured minus premium received)
    # Per-share: capital = strike − credit
    p = 0.0
    _roc_annualized: float = float('nan')
    if credit is not None and credit > 0 and dte > 0:
        capital_per_share = strike - credit
        if capital_per_share > 0:
            roc = (credit / capital_per_share) * (365.0 / dte) * 100.0
            _roc_annualized = round(roc, 2)
            if roc >= 30:
                p = 10.0
            elif roc >= 20:
                p = 7.0 + (roc - 20) / 10.0 * 3.0    # 7 → 10
            elif roc >= 12:
                p = 4.0 + (roc - 12) / 8.0 * 3.0     # 4 → 7
            elif roc >= 6:
                p = 1.0 + (roc - 6) / 6.0 * 3.0      # 1 → 4
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
    import math as _math
    score = 0.0
    bk: dict[str, float] = {}

    # --- Delta bell-curve (15 pts — rescaled from 18 by ×15/18) ---
    p = 0.0
    if not _math.isnan(delta):
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

    # --- Expected Move Buffer (20 pts) — unchanged ---
    p = 0.0
    _cc_em_buffer_pct: float = float('nan')
    if not _math.isnan(iv_used) and iv_used > 0 and dte > 0:
        T = dte / 365.0
        em = current_price * iv_used * _math.sqrt(T)
        em_upper = current_price + em
        sigmas_outside = (strike - em_upper) / em
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
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
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

    # --- Annualized ROC (10 pts) ---
    # CC capital basis = current price (not cost basis — out of scope)
    # Per-share: capital = current_price − credit
    p = 0.0
    _roc_annualized: float = float('nan')
    if credit is not None and credit > 0 and dte > 0 and current_price > 0:
        capital_per_share = current_price - credit
        if capital_per_share > 0:
            roc = (credit / capital_per_share) * (365.0 / dte) * 100.0
            _roc_annualized = round(roc, 2)
            if roc >= 30:
                p = 10.0
            elif roc >= 20:
                p = 7.0 + (roc - 20) / 10.0 * 3.0
            elif roc >= 12:
                p = 4.0 + (roc - 12) / 8.0 * 3.0
            elif roc >= 6:
                p = 1.0 + (roc - 6) / 6.0 * 3.0
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


def compute_ditm_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    premium: float,
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
) -> float:
    """
    DITM Strike Quality Score 0–100.
    Measures how efficient *this specific deep ITM call* is to buy.

    Delta (35):           Deep ITM sweet spot 0.80–0.85 (dropped Moneyness — correlated)
    Extrinsic % (35):     Extrinsic / Stock Price × 100 — lower = less time premium wasted
    Bid-Ask Spread (20):  Execution cost (raised from 15 — key cost for DITM)
    OI / Volume (10):     Liquidity depth at this specific strike
    """
    import math as _math
    score = 0.0

    # --- Delta (35 pts) — sweet spot 0.80–0.85 ---
    if not _math.isnan(delta):
        if 0.80 <= delta <= 0.85:
            score += 35.0
        elif (0.75 <= delta < 0.80) or (0.85 < delta <= 0.90):
            score += 28.0
        elif (0.70 <= delta < 0.75) or (0.90 < delta <= 0.95):
            score += 18.0
        elif (0.65 <= delta < 0.70) or (0.95 < delta < 1.0):
            score += 9.0
        # <0.65: 0

    # --- Extrinsic % (35 pts) — extrinsic / stock price, lower is better ---
    intrinsic = max(0.0, current_price - strike)
    extrinsic = max(0.0, premium - intrinsic)
    extrinsic_pct = (extrinsic / current_price * 100.0) if current_price > 0 else 100.0

    if extrinsic_pct <= 1.0:
        score += 35.0
    elif extrinsic_pct <= 2.0:
        score += 35.0 - (extrinsic_pct - 1.0) * 9.0
    elif extrinsic_pct <= 4.0:
        score += 26.0 - (extrinsic_pct - 2.0) / 2.0 * 12.0
    elif extrinsic_pct <= 6.0:
        score += 14.0 - (extrinsic_pct - 4.0) / 2.0 * 9.0
    elif extrinsic_pct <= 9.0:
        score += 5.0 - (extrinsic_pct - 6.0) / 3.0 * 5.0
    # >9%: 0

    # --- Bid-Ask Spread % (20 pts) ---
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 1.0:
            score += 20.0
        elif bid_ask_spread_pct <= 3.0:
            score += 20.0 - (bid_ask_spread_pct - 1.0) / 2.0 * 7.0
        elif bid_ask_spread_pct <= 5.0:
            score += 13.0 - (bid_ask_spread_pct - 3.0) / 2.0 * 6.0
        elif bid_ask_spread_pct <= 8.0:
            score += 7.0 - (bid_ask_spread_pct - 5.0) / 3.0 * 5.0
        elif bid_ask_spread_pct <= 12.0:
            score += 2.0
        # >12%: 0

    # --- OI / Volume (10 pts) ---
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 500:
        score += 10.0
    elif liquidity_count >= 200:
        score += 10.0 - (500 - liquidity_count) / 300.0 * 4.0
    elif liquidity_count >= 100:
        score += 6.0 - (200 - liquidity_count) / 100.0 * 3.0
    elif liquidity_count >= 50:
        score += 3.0 - (100 - liquidity_count) / 50.0 * 3.0
    # <50: 0

    return round(max(0.0, min(100.0, score)), 1)


def compute_ditm_final_score(env_score: float, strike_score: float) -> float:
    """DITM Final Score = 0.35 × Env + 0.65 × Strike (strike quality dominates for DITM)."""
    return round(0.35 * env_score + 0.65 * strike_score, 1)
