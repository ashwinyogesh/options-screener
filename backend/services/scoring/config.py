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
SCORING_VERSION: str = "3.2.0"

# Environment-score factor weights (CSP/CC). Sum = 100.
# Mirror of the per-factor caps inside `compute_env_score` in `env.py`.
ENV_WEIGHTS: dict[str, float] = {
    'IH':  35.0,   # IV / HV Ratio
    'Tr':  15.0,   # Trend: 52W high distance (direction-aware)
    'SMA':  5.0,   # Trend: SMA alignment (P>SMA50>SMA200 categorical)
    'SLP':  5.0,   # Trend: SMA50 10-day slope (momentum confirmation)
    'RSI': 20.0,   # RSI(14) (direction-aware)
    'OI':  20.0,   # Chain Median OI (circuit breaker)
}
ENV_MAX: float = sum(ENV_WEIGHTS.values())  # 100.0

# Earnings-within-DTE penalty applied on top of the env score.
EARNINGS_PENALTY: float = -15.0

# Strike-score factor weights (CSP/CC). Sum = 100.
STRIKE_WEIGHTS: dict[str, float] = {
    'Δ':   25.0,   # Delta bell-curve (smooth piecewise-linear)
    'BA':  25.0,   # Bid-Ask spread
    'LQ':  15.0,   # OI / Volume circuit breaker (per-strike)
    'ROC': 35.0,   # Annualized return on capital
}
STRIKE_MAX: float = sum(STRIKE_WEIGHTS.values())  # 100.0
