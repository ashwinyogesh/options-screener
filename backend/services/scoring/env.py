"""
Environment scorers — compute the 0–100 "environment" half of the CSP/CC
screeners' final score from a bundle of indicator values.

`compute_env_score` is direction-aware via the `direction` arg ('csp' | 'cc');
it shapes the 52W and RSI curves accordingly.

DITM environment scoring is intentionally *not* in this module yet — the live
implementation lives inline in `services.ditm_service.py`. Phase 4 of the
screener refactor will migrate it here once `ScreenerConfig` exists
(see ADR-0002).
"""
from __future__ import annotations

import math

from .config import EARNINGS_PENALTY

__all__ = ["compute_env_score"]


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
    direction = direction.lower()
    score = 0.0
    bk: dict[str, float] = {}

    # --- HV Rank (22 pts) — rescaled from 30 by ×22/30 ---
    p = 0.0
    if iv_rank is not None and not math.isnan(iv_rank):
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
    if not iv_stale and iv_hv_ratio is not None and not math.isnan(iv_hv_ratio):
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
    if not math.isnan(dist_from_52w_high_pct):
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
    if not math.isnan(rsi):
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
    if not math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(math.log10(chain_median_oi) / math.log10(5000), 1.0) * 8.0
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
