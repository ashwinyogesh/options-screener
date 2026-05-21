"""
Scoring weight tables — v3 lean model (see ADR-0007).

These dicts describe *what* the screener scorers weight. They do **not**
drive the math: `env.py` and `strike.py` hardcode the per-factor caps
inline. The dicts are the textual source-of-truth referenced by
`SCORING_REFERENCE.md` and the frontend `SCORE_LEGEND` arrays.

Per copilot-instructions.md: any tweak to these weights requires an ADR
plus matching updates to `SCORING_REFERENCE.md` and the frontend legend.

SCORING_VERSION is the gold-standard version tag for the current
calibration. Bump it whenever scoring constants change (matches the git
tag). Surfaced by the /health endpoint at runtime.
"""
from __future__ import annotations

# v3.1 = CSP/CC calibration (ADR-0009: trend split, delta bell, BA/ROC)
# v3.2 = DITM de-correlation (trend R², 52W tent, return compress,
#         delta 0.82-0.90, leverage hard cap 5×)
# v3.3 = IV/HV Ratio → IV Percentile (regime-agnostic vol signal)
# v3.4 = CSP Method D (ADR-0011). CSP env drops SMA/SLP/RSI (each had ρ<0
#        or ~0 against realised ROC); IV-percentile weight raised 35→60;
#        the CSP 52W-distance factor is *flipped* (rewards distance FROM
#        the 52W high, not proximity to it) and weight raised 15→20.
#        CSP strike rebalanced: Δ 25→40, BA 25→15, ROC 35→30, LQ 15
#        (unchanged). CC scoring is unchanged from v3.3.
SCORING_VERSION: str = "3.4.0"

# ----- CC Environment weights (unchanged from v3.3) ------------------------
# Sum = 100. Mirror of the per-factor caps inside `compute_env_score(..., direction='cc')`.
ENV_WEIGHTS: dict[str, float] = {
    'IVP': 35.0,   # IV Percentile (% of last-252d where HV < today)
    'Tr':  15.0,   # Trend: 52W high distance (direction-aware tent for CC)
    'SMA':  5.0,   # Trend: SMA alignment (P>SMA50>SMA200 categorical)
    'SLP':  5.0,   # Trend: SMA50 10-day slope (momentum confirmation)
    'RSI': 20.0,   # RSI(14) (direction-aware)
    'OI':  20.0,   # Chain Median OI (circuit breaker)
}
ENV_MAX: float = sum(ENV_WEIGHTS.values())  # 100.0

# ----- CSP Environment weights (v3.4 Method D — ADR-0011) ------------------
# Sum = 100. Mirror of the per-factor caps inside `compute_env_score(..., direction='csp')`.
# Removed factors (SMA, SLP, RSI) had ρ(factor, realised ROC) ≤ 0 across a
# 7,085-trade 3-year backtest — they were degrading the rank. The CSP Tr
# factor is flipped: stocks near their 52W high had WORSE realised ROC and
# larger loss-given-assignment than stocks far below.
CSP_ENV_WEIGHTS: dict[str, float] = {
    'IVP': 60.0,   # IV Percentile (regime-agnostic vol signal)
    'Tr':  20.0,   # 52W high *distance* — flipped: far-from-high = more credit
    'OI':  20.0,   # Chain Median OI (circuit breaker)
}
CSP_ENV_MAX: float = sum(CSP_ENV_WEIGHTS.values())  # 100.0

# Earnings-within-DTE penalty applied on top of the env score.
EARNINGS_PENALTY: float = -15.0

# ----- CC Strike weights (unchanged from v3.3) -----------------------------
STRIKE_WEIGHTS: dict[str, float] = {
    'Δ':   25.0,   # Delta bell-curve (smooth piecewise-linear)
    'BA':  25.0,   # Bid-Ask spread
    'LQ':  15.0,   # OI / Volume circuit breaker (per-strike)
    'ROC': 35.0,   # Annualized return on capital
}
STRIKE_MAX: float = sum(STRIKE_WEIGHTS.values())  # 100.0

# ----- CSP Strike weights (v3.4 Method D) ----------------------------------
# Δ raised 25→40 (best capital-safety lever — close to ideal delta = lower
# assignment); ROC lowered 35→30 (premium chase was rewarding into-the-money
# overrides); BA lowered 25→15 (effect on realised ROC was marginal); LQ
# unchanged. Sum = 100.
CSP_STRIKE_WEIGHTS: dict[str, float] = {
    'Δ':   40.0,
    'BA':  15.0,
    'LQ':  15.0,
    'ROC': 30.0,
}
CSP_STRIKE_MAX: float = sum(CSP_STRIKE_WEIGHTS.values())  # 100.0

# ---------------------------------------------------------------------------
# DITM factor weights (v3.2 lean model — ADR-0008)
# Mirror of per-factor caps inside `compute_ditm_env_score` / `compute_ditm_strike_score`.
# ---------------------------------------------------------------------------

# DITM Environment-score factor weights. Sum = 100.
DITM_ENV_WEIGHTS: dict[str, float] = {
    'Tr':   25.0,  # Trend strength: SMA alignment (soft, proportional)
    'Ret':  15.0,  # 200-day return (compressed v3.2; was 25)
    '52W':  20.0,  # 52W high distance — tent curve, sweet spot 3–12%
    'R2':   10.0,  # Trend stability: R² of 50-day OLS regression (v3.2 NEW)
    'WRSI': 15.0,  # Weekly RSI(14), direction-aware pullback credit
    'LQ':   15.0,  # Chain median OI in 0.60–0.95 delta band (log scale)
}
DITM_ENV_MAX: float = sum(DITM_ENV_WEIGHTS.values())  # 100.0

# Earnings penalty applied on top of the DITM env score (DTE-scaled).
DITM_EARNINGS_PENALTY: float = -15.0  # full penalty; halved at 8–14 DTE

# DITM Strike-score factor weights. Sum = 100.
DITM_STRIKE_WEIGHTS: dict[str, float] = {
    '\u0394':   20.0,  # Delta position — sweet spot 0.82–0.90
    'Lev': 25.0,  # Leverage = delta × price / mid — flat top 2.5–4×; hard 0 ≥5×
    'Ext': 25.0,  # Extrinsic % of strike — lower is better (DITM quality signal)
    'BA':  20.0,  # Bid-Ask spread % of mid
    'IV':  10.0,  # IV Percentile — lower is cheaper for the buyer
}
DITM_STRIKE_MAX: float = sum(DITM_STRIKE_WEIGHTS.values())  # 100.0
