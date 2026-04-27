"""
Environment scorers — compute the 0–100 "environment" half of each screener's
final score from a bundle of indicator values.

CSP/CC use `compute_env_score` (direction-aware via the `direction` arg).
DITM uses its own `compute_ditm_env_score` because its weights and gates are
fundamentally different (LEAPS-buying is a different thesis than premium-selling).
"""
from __future__ import annotations

from .config import EARNINGS_PENALTY


def compute_env_score(
    *,
    iv_rank: float | None,             # HV Rank (kept name for back-compat at call sites)
    iv_hv_ratio: float | None,
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    direction: str = 'csp',            # 'csp' or 'cc' — affects 52W and RSI curves
    dte: int | None = None,            # Required for DTE sweet-spot factor
    iv_stale: bool = False,            # If True, IV/HV pts forced to 0
) -> tuple[float, str]:
    """
    Environment Score 0–100 (+earnings penalty up to −15).
    Direction-aware: CSP rewards strength/momentum, CC rewards consolidation.

    Weights:  HV Rank 22 + IV/HV 28 + SMA 15 + 52W 10 + RSI 10 + Chain OI 8 + DTE 7 = 100
    Penalty:  Earnings within DTE = −15

    Note: parameter `iv_rank` is the HV Rank value (30d HV ranked over 252d).
    The name is preserved for call-site compatibility but the field is HV-derived.
    """
    import math as _math
    direction = direction.lower()
    score = 0.0
    bk: dict[str, float] = {}

    # --- HV Rank (22 pts) — rescaled from 30 by ×22/30 ---
    p = 0.0
    if iv_rank is not None and not _math.isnan(iv_rank):
        if iv_rank >= 80:
            p = 22.0
        elif iv_rank >= 60:
            p = 13.2 + (iv_rank - 60) / 20.0 * 5.13   # 13.2 → 18.33
        elif iv_rank >= 40:
            p = 6.6 + (iv_rank - 40) / 20.0 * 6.6     # 6.6 → 13.2
        elif iv_rank >= 20:
            p = (iv_rank - 20) / 20.0 * 6.6           # 0 → 6.6
    score += p; bk['HV'] = p

    # --- IV / HV Ratio (28 pts) — rescaled from 25 by ×28/25 = 1.12 ---
    # Stale-IV: when iv_stale=True (IV NaN or ≤0.01), award 0 pts and let UI flag the row.
    p = 0.0
    if not iv_stale and iv_hv_ratio is not None and not _math.isnan(iv_hv_ratio):
        if iv_hv_ratio >= 1.7:
            p = 28.0
        elif iv_hv_ratio >= 1.4:
            p = 14.0 + (iv_hv_ratio - 1.4) / 0.3 * 8.4    # 14.0 → 22.4
        elif iv_hv_ratio >= 1.1:
            p = 6.72 + (iv_hv_ratio - 1.1) / 0.3 * 7.28   # 6.72 → 14.0
        elif iv_hv_ratio >= 0.9:
            p = 2.8 + (iv_hv_ratio - 0.9) / 0.2 * 3.92    # 2.8 → 6.72
        elif iv_hv_ratio >= 0.8:
            p = (iv_hv_ratio - 0.8) / 0.1 * 2.8           # 0 → 2.8
    score += p; bk['IH'] = p

    # --- SMA Alignment (15 pts): categorical, unchanged ---
    p = 0.0
    if price_above_sma50 and sma50_above_sma200:
        p = 15.0
    elif price_above_sma50:
        p = 9.0
    elif sma50_above_sma200:
        p = 5.0
    score += p; bk['SMA'] = p

    # --- 52W High Distance (10 pts) — direction-aware ---
    p = 0.0
    if not _math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if direction == 'cc':
            # CC: reward consolidation (5–15% below). Smooth ramps, no plateaus.
            if pct_below <= 5:
                p = 4.0
            elif pct_below <= 15:
                p = 4.0 + (pct_below - 5.0) / 10.0 * 6.0   # 4 → 10
            elif pct_below <= 25:
                p = 10.0 - (pct_below - 15.0) / 10.0 * 4.0  # 10 → 6
            elif pct_below <= 35:
                p = 6.0 - (pct_below - 25.0) / 10.0 * 4.0   # 6 → 2
            # > 35% → 0
        else:
            # CSP: reward strength near the high. Rescaled from 15 → 10 (×10/15).
            if pct_below <= 5:
                p = 10.0
            elif pct_below <= 10:
                p = 7.333 - (pct_below - 5.0) / 5.0 * 2.667   # 7.333 → 4.667
            elif pct_below <= 20:
                p = 4.667 - (pct_below - 10.0) / 10.0 * 2.667  # 4.667 → 2.0
            elif pct_below <= 30:
                p = 2.0 - (pct_below - 20.0) / 10.0 * 2.0     # 2.0 → 0
    score += p; bk['52W'] = p

    # --- RSI(14) (10 pts) — direction-aware ---
    p = 0.0
    if not _math.isnan(rsi):
        if direction == 'cc':
            # CC sweet spot 38–58 (mild weakness favors call sellers)
            # Steeper ceiling decay — overheated stocks blow through call strikes
            if 38 <= rsi <= 58:
                p = 10.0
            elif 30 <= rsi < 38:
                p = 4.0 + (rsi - 30.0) / 8.0 * 6.0      # 4 → 10
            elif 58 < rsi <= 70:
                p = 10.0 - (rsi - 58.0) / 12.0 * 10.0   # 10 → 0
            # < 30 or > 70 → 0
        else:
            # CSP: unchanged sweet spot 42–62
            if 42 <= rsi <= 62:
                p = 10.0
            elif 35 <= rsi < 42:
                p = 6.0 + (rsi - 35.0) / 7.0 * 4.0
            elif 62 < rsi <= 75:
                p = 10.0 * (75.0 - rsi) / 13.0
            elif 30 <= rsi < 35:
                p = 2.0
    score += p; bk['RSI'] = p

    # --- Chain Median OI (8 pts — circuit breaker, rescaled from 5 by ×8/5) ---
    p = 0.0
    if not _math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(_math.log10(chain_median_oi) / _math.log10(5000), 1.0) * 8.0
    score += p; bk['OI'] = p

    # --- DTE Sweet Spot (7 pts) ---
    p = 0.0
    if dte is not None and dte > 0:
        if 30 <= dte <= 45:
            p = 7.0
        elif 21 <= dte < 30 or 45 < dte <= 60:
            p = 4.2
        elif 14 <= dte < 21 or 60 < dte <= 75:
            p = 2.1
    score += p; bk['DTE'] = p

    # --- Earnings penalty ---
    earn_p = 0.0
    if earnings_within_dte:
        earn_p = EARNINGS_PENALTY
        score += earn_p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p != 0:
        detail += f' Ear:{round(earn_p)}'
    return round(score, 1), detail


def compute_ditm_env_score(
    *,
    iv_hv_ratio: float | None,
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    sma50_slope_pct: float | None,
    dist_from_52w_high_pct: float,
    trend_persistence: float | None,
    chain_median_oi: float,
    days_to_earnings: int | None,
    iv_rank: float | None,
) -> float:
    """
    DITM Environment Score 0–100.
    For BUYING calls: LOW IV is good (cheap premium), STRONG TREND is critical.

    IV Cheapness (45):        IV/HV Ratio inverted (sole IV metric — edge vs realized vol)
    Trend Strength (30):      SMA Alignment + SMA50 Slope + 52W High Proximity (composite)
    Trend Persistence (10):   % of last 60 sessions above SMA50 (LEAPS-appropriate momentum)
    Liquidity (10):           Chain Median OI
    Earnings penalty:         tiered, softened when IV Rank >50 (already priced in)
    """
    import math as _math
    score = 0.0

    # --- IV/HV Ratio INVERTED (45 pts) — sole IV metric ---
    # Measures edge: IV < HV means options cheaper than what stock actually moves
    # <0.7=45, 0.7–0.9 linear 45→27, 0.9–1.1 linear 27→13, 1.1–1.5 linear 13→2, >1.5=0
    if iv_hv_ratio is not None and not _math.isnan(iv_hv_ratio):
        if iv_hv_ratio < 0.7:
            score += 45.0
        elif iv_hv_ratio < 0.9:
            score += 45.0 - (iv_hv_ratio - 0.7) / 0.2 * 18.0
        elif iv_hv_ratio < 1.1:
            score += 27.0 - (iv_hv_ratio - 0.9) / 0.2 * 14.0
        elif iv_hv_ratio < 1.5:
            score += 13.0 - (iv_hv_ratio - 1.1) / 0.4 * 11.0
        # >=1.5: 0 pts

    # --- Trend Strength Composite (30 pts) ---
    # SMA Alignment: 15 pts
    if price_above_sma50 and sma50_above_sma200:
        score += 15.0
    elif price_above_sma50:
        score += 9.0
    elif sma50_above_sma200:
        score += 4.0

    # SMA50 Slope: 7 pts (positive and rising = uptrend has momentum)
    if sma50_slope_pct is not None and not _math.isnan(sma50_slope_pct):
        if sma50_slope_pct > 1.0:
            score += 7.0
        elif sma50_slope_pct > 0.3:
            score += 7.0 - (1.0 - sma50_slope_pct) / 0.7 * 2.0
        elif sma50_slope_pct > 0.0:
            score += 5.0 - (0.3 - sma50_slope_pct) / 0.3 * 3.0
        elif sma50_slope_pct > -0.5:
            score += 1.0
        # <= -0.5%: 0 — declining SMA50

    # 52W High Proximity: 8 pts
    if not _math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if pct_below <= 5:
            score += 8.0
        elif pct_below <= 15:
            score += 8.0 - (pct_below - 5) / 10.0 * 5.0
        elif pct_below <= 30:
            score += 3.0 - (pct_below - 15) / 15.0 * 3.0
        # >30%: 0

    # --- Trend Persistence (10 pts) — % of last 60 sessions above SMA50 ---
    # Replaces RSI(14): better signal for LEAPS horizon
    # >=75%=10, 60–75 linear 10→6, 50–60 linear 6→3, 40–50=1, <40=0
    if trend_persistence is not None and not _math.isnan(trend_persistence):
        if trend_persistence >= 75:
            score += 10.0
        elif trend_persistence >= 60:
            score += 10.0 - (75 - trend_persistence) / 15.0 * 4.0
        elif trend_persistence >= 50:
            score += 6.0 - (60 - trend_persistence) / 10.0 * 3.0
        elif trend_persistence >= 40:
            score += 1.0
        # <40%: 0 — stock spends too much time below SMA50

    # --- Chain Median OI (10 pts) — log scale ---
    if not _math.isnan(chain_median_oi) and chain_median_oi > 0:
        score += min(_math.log10(chain_median_oi) / _math.log10(5000), 1.0) * 10.0

    # --- Earnings penalty (tiered; softened when IV Rank >50 = already priced) ---
    if days_to_earnings is not None and days_to_earnings >= 0:
        if days_to_earnings < 14:
            penalty = 15.0 if (iv_rank is None or iv_rank <= 50) else 8.0
        elif days_to_earnings < 30:
            penalty = 8.0 if (iv_rank is None or iv_rank <= 50) else 4.0
        elif days_to_earnings < 60:
            penalty = 3.0 if (iv_rank is None or iv_rank <= 50) else 1.0
        else:
            penalty = 0.0
        score -= penalty

    return round(score, 1)
