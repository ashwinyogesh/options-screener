"""
Environment scorer — v3.1 calibration (see ADR-0007 + ADR-0009).

`compute_env_score` is direction-aware via the `direction` arg ('csp' | 'cc');
it shapes the Trend (52W), SMA, and RSI curves accordingly.

v3 reduced ENV from 7 factors to 4. v3.1 splits the 25-pt Trend factor into
three independent signals (total still 25 pts, total ENV still 100):
  Tr:  15 pts  52W high distance  — momentum proxy, direction-aware
  SMA:  5 pts  SMA alignment categorical (P>SMA50>SMA200)
  SLP:  5 pts  SMA50 10-day slope  — momentum confirmation

The legacy parameters `iv_rank`, `price_above_sma50`, `sma50_above_sma200`,
and `dte` are kept in the signature for back-compat but ignored.

DITM environment scoring is intentionally *not* in this module yet — the
live implementation lives inline in `services.ditm_service.py`.
"""
from __future__ import annotations

import math

from .config import EARNINGS_PENALTY

__all__ = ["compute_env_score"]


def compute_env_score(
    *,
    iv_rank: float | None,             # IGNORED (HV Rank dropped) — kept for back-compat
    iv_hv_ratio: float | None,
    price_above_sma50: bool,           # IGNORED (raw bool; use sma_ratio for scoring)
    sma50_above_sma200: bool,          # IGNORED (raw bool; use sma_ratio for scoring)
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    direction: str = 'csp',            # 'csp' or 'cc' — affects Trend and RSI curves
    dte: int | None = None,            # IGNORED (DTE Sweet Spot dropped) — kept for back-compat
    iv_stale: bool = False,            # If True, IV/HV pts forced to 0
    sma_ratio: float = 1.0,            # v3.1: SMA50/SMA200 ratio (1.0 = neutral default)
    sma50_slope_pct: float = 0.0,      # v3.1: SMA50 10-day % change (0.0 = flat default)
) -> tuple[float, str]:
    """
    Environment Score 0–100 (+earnings penalty up to −15).
    Direction-aware: CSP rewards strength near 52W high; CC rewards
    consolidation 5–15% below the high.

    Weights (v3.1): IV/HV 35 + Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20 = 100
    Penalty: Earnings within DTE = −15

    Back-compat: `iv_rank`, `price_above_sma50`, `sma50_above_sma200`, `dte`
    are accepted but unused.
    """
    _ = iv_rank, price_above_sma50, sma50_above_sma200, dte  # explicitly unused
    direction = direction.lower()
    score = 0.0
    bk: dict[str, float] = {}

    # --- IV / HV Ratio (35 pts) — v3 rescale of v2 0.8/1.0/1.1/1.2/1.3 curve ---
    # Stale-IV: when iv_stale=True (IV NaN or ≤0.01), award 0 pts and let UI flag the row.
    p = 0.0
    if not iv_stale and iv_hv_ratio is not None and not math.isnan(iv_hv_ratio):
        if iv_hv_ratio >= 1.3:
            p = 35.0
        elif iv_hv_ratio >= 1.2:
            p = 22.5 + (iv_hv_ratio - 1.2) / 0.1 * 12.5    # 22.5 → 35.0
        elif iv_hv_ratio >= 1.1:
            p = 12.5 + (iv_hv_ratio - 1.1) / 0.1 * 10.0    # 12.5 → 22.5
        elif iv_hv_ratio >= 1.0:
            p = 5.0 + (iv_hv_ratio - 1.0) / 0.1 * 7.5      # 5.0 → 12.5
        elif iv_hv_ratio >= 0.8:
            p = (iv_hv_ratio - 0.8) / 0.2 * 5.0            # 0 → 5.0
    score += p; bk['IH'] = p

    # --- Trend: 52W High Distance (15 pts) — direction-aware, smooth curves ---
    # v3.1: scaled from 25 to 15 pts (10 pts moved to SMA+Slope sub-factors).
    # Smooth: flat top then single linear decay (CSP) / smooth tent (CC).
    # No slope discontinuities: 9.9% and 10.1% below high differ by <0.15 pts.
    p = 0.0
    if not math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if direction == 'cc':
            # CC: sweet spot 5–15% consolidation; smooth tent both sides.
            if pct_below <= 5:
                p = 0.0
            elif pct_below <= 15:
                p = (pct_below - 5.0) / 10.0 * 15.0          # 0 → 15
            elif pct_below <= 35:
                p = max(0.0, 15.0 * (1.0 - (pct_below - 15.0) / 20.0))  # 15 → 0
        else:
            # CSP: flat top ≤5%, single linear decay to 0 at 30%.
            if pct_below <= 5:
                p = 15.0
            else:
                p = max(0.0, 15.0 * (1.0 - (pct_below - 5.0) / 25.0))
    score += p; bk['Tr'] = p

    # --- Trend: SMA Alignment (5 pts) — v3.1 restored signal ---
    # Categorical: P>SMA50>SMA200 is structurally different from 52W proximity.
    # Back-compat booleans are still ignored; sma_ratio encodes the same info
    # continuously: >1.02 = P>SMA50>SMA200 likely; 1.0–1.02 = borderline; etc.
    p = 0.0
    if not math.isnan(sma_ratio):
        if sma_ratio > 1.02:
            p = 5.0
        elif sma_ratio >= 1.0:
            p = 3.0
        elif sma_ratio >= 0.98:
            p = 1.5
    score += p; bk['SMA'] = p

    # --- Trend: SMA50 Slope (5 pts) — v3.1 momentum confirmation ---
    # 10-day % change in SMA50. Rewards accelerating uptrend; zeroes on flat/declining.
    p = 0.0
    if not math.isnan(sma50_slope_pct):
        slp = sma50_slope_pct
        if slp >= 0.5:
            p = 5.0
        elif slp >= 0.2:
            p = 3.0 + (slp - 0.2) / 0.3 * 2.0   # 3.0 → 5.0
        elif slp >= 0.0:
            p = slp / 0.2 * 3.0                   # 0 → 3.0
    score += p; bk['SLP'] = p

    # --- RSI(14) (20 pts) — direction-aware, cliff-fixed (#2, #8) ---
    p = 0.0
    if not math.isnan(rsi):
        if direction == 'cc':
            # CC sweet spot 38–58; smooth ramps both sides.
            # Ceiling extended from 70 to 75 (fix #8) so AAPL/MSFT in normal trends
            # (RSI 62–68) earn meaningful pts.
            if 38 <= rsi <= 58:
                p = 20.0
            elif 30 <= rsi < 38:
                p = (rsi - 30.0) / 8.0 * 20.0      # 0 → 20 (continuous)
            elif 58 < rsi <= 75:
                p = (75.0 - rsi) / 17.0 * 20.0     # 20 → 0
        else:
            # CSP sweet spot 42–62; smooth ramps both sides.
            # Cliff fix #2: removed the 30–35 floor of 2 pts that created a
            # 4-pt jump at RSI=35. Now: <35 = 0, 35–42 ramps continuously.
            if 42 <= rsi <= 62:
                p = 20.0
            elif 35 <= rsi < 42:
                p = (rsi - 35.0) / 7.0 * 20.0      # 0 → 20 (continuous)
            elif 62 < rsi <= 75:
                p = (75.0 - rsi) / 13.0 * 20.0     # 20 → 0
    score += p; bk['RSI'] = p

    # --- Chain Median OI (20 pts) — circuit breaker, scaled from 8 ---
    p = 0.0
    if not math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(math.log10(chain_median_oi) / math.log10(5000), 1.0) * 20.0
    score += p; bk['OI'] = p

    # --- Earnings penalty (applied on top) ---
    earn_p = 0.0
    if earnings_within_dte:
        earn_p = EARNINGS_PENALTY
        score += earn_p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p != 0:
        detail += f' Ear:{round(earn_p)}'
    return round(score, 1), detail
