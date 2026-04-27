"""
Compatibility shim — re-exports from the new layout.

Phase 1 of the screener refactor split this file into:
    - services/indicators.py      — pure indicator functions
    - services/scoring/config.py  — weight constants
    - services/scoring/env.py     — environment scorers
    - services/scoring/strike.py  — strike-quality scorers + final-blend helpers

This shim keeps existing `from services.technical_service import ...` callers
working unchanged. New code should import from the canonical modules above.
The shim will be removed after Phase 5 (router migration), once all call sites
have been updated.
"""
from __future__ import annotations

from services.indicators import (  # noqa: F401
    compute_bollinger,
    compute_dist_from_sma200,
    compute_iv_rank_percentile,
    compute_macd,
    compute_price_vs_52w_high,
    compute_price_vs_sma,
    compute_roc,
    compute_rsi,
    compute_rvol,
    compute_sma_ratio,
    compute_sma20_slope,
    compute_trend_data,
    compute_trend_persistence,
    compute_volume_resistance,
    compute_volume_support,
)
from services.scoring.config import (  # noqa: F401
    EARNINGS_PENALTY,
    ENV_MAX,
    ENV_WEIGHTS,
    STRIKE_MAX,
    STRIKE_WEIGHTS,
)
from services.scoring.env import (  # noqa: F401
    compute_ditm_env_score,
    compute_env_score,
)
from services.scoring.strike import (  # noqa: F401
    compute_cc_final_score,
    compute_cc_strike_score,
    compute_csp_final_score,
    compute_csp_strike_score,
    compute_ditm_final_score,
    compute_ditm_strike_score,
)

__all__ = [
    # indicators
    "compute_bollinger",
    "compute_dist_from_sma200",
    "compute_iv_rank_percentile",
    "compute_macd",
    "compute_price_vs_52w_high",
    "compute_price_vs_sma",
    "compute_roc",
    "compute_rsi",
    "compute_rvol",
    "compute_sma_ratio",
    "compute_sma20_slope",
    "compute_trend_data",
    "compute_trend_persistence",
    "compute_volume_resistance",
    "compute_volume_support",
    # scoring config
    "EARNINGS_PENALTY",
    "ENV_MAX",
    "ENV_WEIGHTS",
    "STRIKE_MAX",
    "STRIKE_WEIGHTS",
    # scoring functions
    "compute_cc_final_score",
    "compute_cc_strike_score",
    "compute_csp_final_score",
    "compute_csp_strike_score",
    "compute_ditm_env_score",
    "compute_ditm_final_score",
    "compute_ditm_strike_score",
    "compute_env_score",
]
