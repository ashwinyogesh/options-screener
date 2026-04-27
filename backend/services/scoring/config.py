"""
Scoring weight constants — single source of truth.

Each weight dict must sum to 100. Modifying these values changes the screener's
identity; per copilot-instructions.md any tweak requires an ADR + matching
update to `SCORING_REFERENCE.md` and the frontend `SCORE_LEGEND` arrays.
"""
from __future__ import annotations


ENV_WEIGHTS: dict[str, float] = {
    'HV':  22.0,   # HV Rank (formerly mislabeled "IV Rank") — uses 30d HV proxy
    'IH':  28.0,   # IV / HV Ratio
    'SMA': 15.0,   # SMA Alignment
    '52W': 10.0,   # 52W High Distance (direction-aware)
    'RSI': 10.0,   # RSI (direction-aware)
    'OI':   8.0,   # Chain Median OI (circuit breaker)
    'DTE':  7.0,   # DTE sweet spot
}
ENV_MAX: float = sum(ENV_WEIGHTS.values())  # 100.0
EARNINGS_PENALTY: float = -15.0

STRIKE_WEIGHTS: dict[str, float] = {
    'Δ':   15.0,   # Delta bell-curve
    'SR':  18.0,   # Distance vs Support / Resistance
    'EM':  20.0,   # Expected Move Buffer
    'OTM':  9.0,   # % OTM from spot
    'BA':  23.0,   # Bid-Ask spread
    'LQ':   5.0,   # OI/Volume circuit breaker
    'ROC': 10.0,   # Annualized return on capital
}
STRIKE_MAX: float = sum(STRIKE_WEIGHTS.values())  # 100.0
