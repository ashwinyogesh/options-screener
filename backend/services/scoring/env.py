"""
Environment scorer — direction-aware (CSP v3.4 Method D / CC v3.3).

`compute_env_score` dispatches to one of two scoring profiles based on the
`direction` arg:

  - direction='csp' → v3.4 Method D (ADR-0011):
        IVP 60 + Tr_flipped 20 + OI 20 = 100
        (SMA/SLP/RSI dropped; Tr rewards distance FROM 52W high)

  - direction='cc'  → v3.3 (ADR-0007 / ADR-0009):
        IVP 35 + Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20 = 100
        (Tr is a tent: 5–15% below high is the sweet spot)

The legacy parameters `iv_rank`, `iv_hv_ratio`, `price_above_sma50`,
`sma50_above_sma200`, `dte`, and `iv_stale` are accepted in the signature
for back-compat but no longer affect the score.

DITM environment scoring lives in `services.scoring.ditm`.
"""
from __future__ import annotations

import math

from .config import EARNINGS_PENALTY

__all__ = ["compute_env_score"]


def compute_env_score(
    *,
    iv_rank: float | None,             # IGNORED (HV Rank dropped) — kept for back-compat
    iv_hv_ratio: float | None,         # IGNORED in scoring (v3.3) — kept for back-compat/display
    price_above_sma50: bool,           # IGNORED (raw bool; use sma_ratio for scoring)
    sma50_above_sma200: bool,          # IGNORED (raw bool; use sma_ratio for scoring)
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    direction: str = 'csp',            # 'csp' (v3.4 Method D) or 'cc' (v3.3)
    dte: int | None = None,            # IGNORED (DTE Sweet Spot dropped) — kept for back-compat
    iv_stale: bool = False,            # IGNORED in scoring (v3.3+) — percentile is HV-derived
    sma_ratio: float = 1.0,            # used by CC only (v3.3); ignored by CSP (v3.4)
    sma50_slope_pct: float = 0.0,      # used by CC only (v3.3); ignored by CSP (v3.4)
    iv_percentile: float | None = None,
) -> tuple[float, str]:
    """
    Environment Score 0–100 (+earnings penalty up to −15). Dispatches by
    `direction` to either the CSP Method-D scorer (v3.4) or the CC scorer (v3.3).
    """
    _ = iv_rank, iv_hv_ratio, price_above_sma50, sma50_above_sma200, dte, iv_stale  # explicitly unused
    if direction.lower() == 'cc':
        return _compute_env_score_cc_v33(
            dist_from_52w_high_pct=dist_from_52w_high_pct,
            rsi=rsi,
            chain_median_oi=chain_median_oi,
            earnings_within_dte=earnings_within_dte,
            sma_ratio=sma_ratio,
            sma50_slope_pct=sma50_slope_pct,
            iv_percentile=iv_percentile,
        )
    return _compute_env_score_csp_v34(
        dist_from_52w_high_pct=dist_from_52w_high_pct,
        chain_median_oi=chain_median_oi,
        earnings_within_dte=earnings_within_dte,
        iv_percentile=iv_percentile,
    )


# ---------------------------------------------------------------------------
# CSP v3.4 Method D scorer — ADR-0011
# Weights: IVP 60 + Tr_flipped 20 + OI 20 = 100   (SMA/SLP/RSI dropped)
# ---------------------------------------------------------------------------


def _compute_env_score_csp_v34(
    *,
    dist_from_52w_high_pct: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    iv_percentile: float | None,
) -> tuple[float, str]:
    """
    CSP env score under Method D.

    - IVP (60 pts): same shape as v3.3, rescaled ×60/35.
      <30th=0 · 30–50th→0→17.1 · 50–75th→17.1→42.9 · 75–90th→42.9→60 · ≥90th=60.
    - Tr_flipped (20 pts): rewards distance FROM 52W high (the v3.3 CSP
      direction had ρ(Tr, realised ROC) = −0.29 in backtest — empirically
      wrong-signed).
      pct_below ≤ 5%: 0   ·   5–30%: linear 0 → 20   ·   >30%: clamp 20.
    - OI (20 pts): unchanged from v3.3.
    - Earnings: −15 penalty if within DTE.
    """
    score = 0.0
    bk: dict[str, float] = {}

    # IVP — 60 pts (same curve as v3.3, rescaled).
    p = 0.0
    if iv_percentile is not None and not math.isnan(iv_percentile):
        pct = iv_percentile
        if pct >= 90:
            p = 35.0
        elif pct >= 75:
            p = 25.0 + (pct - 75.0) / 15.0 * 10.0
        elif pct >= 50:
            p = 10.0 + (pct - 50.0) / 25.0 * 15.0
        elif pct >= 30:
            p = (pct - 30.0) / 20.0 * 10.0
    p = p * (60.0 / 35.0)
    score += p
    bk['IVP'] = p

    # Tr_flipped — 20 pts. Mirror of v3.3 CSP curve.
    p = 0.0
    if not math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if pct_below <= 5:
            p = 0.0
        elif pct_below <= 30:
            p = (pct_below - 5.0) / 25.0 * 20.0   # 0 → 20
        else:
            p = 20.0
    score += p
    bk['Tr'] = p

    # OI — 20 pts (unchanged).
    p = 0.0
    if not math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(math.log10(chain_median_oi) / math.log10(5000), 1.0) * 20.0
    score += p
    bk['OI'] = p

    # Earnings penalty.
    earn_p = 0.0
    if earnings_within_dte:
        earn_p = EARNINGS_PENALTY
        score += earn_p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p != 0:
        detail += f' Ear:{round(earn_p)}'
    return round(score, 1), detail


# ---------------------------------------------------------------------------
# CC v3.3 scorer — kept verbatim (only the CSP path changed for v3.4)
# Weights: IVP 35 + Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20 = 100
# ---------------------------------------------------------------------------


def _compute_env_score_cc_v33(
    *,
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    sma_ratio: float,
    sma50_slope_pct: float,
    iv_percentile: float | None,
) -> tuple[float, str]:
    """CC env score under v3.3 — unchanged."""
    score = 0.0
    bk: dict[str, float] = {}

    # --- IV Percentile (35 pts) ---
    p = 0.0
    if iv_percentile is not None and not math.isnan(iv_percentile):
        pct = iv_percentile
        if pct >= 90:
            p = 35.0
        elif pct >= 75:
            p = 25.0 + (pct - 75.0) / 15.0 * 10.0
        elif pct >= 50:
            p = 10.0 + (pct - 50.0) / 25.0 * 15.0
        elif pct >= 30:
            p = (pct - 30.0) / 20.0 * 10.0
    score += p; bk['IVP'] = p

    # --- Trend: 52W (15 pts) — CC tent (5–15% sweet spot) ---
    p = 0.0
    if not math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if pct_below <= 5:
            p = 0.0
        elif pct_below <= 15:
            p = (pct_below - 5.0) / 10.0 * 15.0          # 0 → 15
        elif pct_below <= 35:
            p = max(0.0, 15.0 * (1.0 - (pct_below - 15.0) / 20.0))  # 15 → 0
    score += p; bk['Tr'] = p

    # --- SMA Alignment (5 pts) ---
    p = 0.0
    if not math.isnan(sma_ratio):
        if sma_ratio > 1.02:
            p = 5.0
        elif sma_ratio >= 1.0:
            p = 3.0
        elif sma_ratio >= 0.98:
            p = 1.5
    score += p; bk['SMA'] = p

    # --- SMA50 Slope (5 pts) ---
    p = 0.0
    if not math.isnan(sma50_slope_pct):
        slp = sma50_slope_pct
        if slp >= 0.5:
            p = 5.0
        elif slp >= 0.2:
            p = 3.0 + (slp - 0.2) / 0.3 * 2.0
        elif slp >= 0.0:
            p = slp / 0.2 * 3.0
    score += p; bk['SLP'] = p

    # --- RSI (20 pts) — CC sweet spot 38–58, ceiling 75 ---
    p = 0.0
    if not math.isnan(rsi):
        if 38 <= rsi <= 58:
            p = 20.0
        elif 30 <= rsi < 38:
            p = (rsi - 30.0) / 8.0 * 20.0
        elif 58 < rsi <= 75:
            p = (75.0 - rsi) / 17.0 * 20.0
    score += p; bk['RSI'] = p

    # --- Chain Median OI (20 pts) ---
    p = 0.0
    if not math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(math.log10(chain_median_oi) / math.log10(5000), 1.0) * 20.0
    score += p; bk['OI'] = p

    # --- Earnings penalty ---
    earn_p = 0.0
    if earnings_within_dte:
        earn_p = EARNINGS_PENALTY
        score += earn_p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p != 0:
        detail += f' Ear:{round(earn_p)}'
    return round(score, 1), detail
