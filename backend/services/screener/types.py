"""
Protocol types for the unified screener.

This module defines the type surface that `ScreenerConfig` and the (future)
`runner.run(...)` will consume. **No logic.** Concrete screeners (CSP / CC /
DITM) supply the callables; the runner stays agnostic.

Design notes (see plan-screener-refactor.md, Phase 2):
- `Indicators` and `StrikeContext` are **union bundles** — they contain every
  field any of the three live scorers reads. Each scorer ignores the fields
  it doesn't use. This is the price of one runner over three; the alternative
  was a per-screener bundle, which would push branching back into the runner.
- All three concrete env/strike scorers diverge in arity (CSP/CC vs DITM in
  particular). The `EnvScorer` / `StrikeScorer` callable types take the union
  bundle so the runner has a single, stable call site.
- Fields default to `None` / sentinel where a screener doesn't populate them.
  Scorers that *require* a field must validate it themselves and raise — the
  runner does not police bundle completeness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

# --- Literals --------------------------------------------------------------

Direction = Literal["short_put", "short_call", "long_call"]
"""High-level screener orientation. Maps 1:1 to (csp, cc, ditm)."""


# --- Indicator + strike context bundles ------------------------------------

@dataclass(frozen=True)
class Indicators:
    """
    Per-symbol environment inputs consumed by the env scorer.

    Strict scope: only fields a `compute_*_env_score` function reads, plus
    the per-symbol levels (`vol_support_*`, `vol_resistance_*`) consumed at
    strike-scoring time via `strike_context_builder`. Render-only metadata
    (BB, sma_ratio, hv_sigma, iv_percentile, earnings_date) lives on
    `SymbolMetrics`, not here — see ADR-0007.

    Optional fields default to None so a CSP config can leave `weekly_rsi`
    unset and a DITM config can leave `iv_hv_ratio` unset.
    """

    # Common (all three screeners)
    price: float
    sma50: float
    sma200: float
    price_above_sma50: bool
    sma50_above_sma200: bool
    dist_from_52w_high_pct: float
    chain_median_oi: float
    earnings_within_dte: bool
    days_to_earnings: Optional[int]
    dte: int

    # CSP / CC only
    iv_hv_ratio: Optional[float] = None
    iv_stale: bool = False
    rsi: Optional[float] = None            # RSI(14) daily

    # Shared by all three (CSP/CC env scorer historically called the param
    # `iv_rank`, but the value is HV-derived; we standardise on `hv_rank` at
    # this layer and Phase 3 will adapt scorer call sites).
    hv_rank: Optional[float] = None

    # DITM only
    weekly_rsi: Optional[float] = None
    ret_200d_frac: Optional[float] = None  # 200-day median-anchored return as fraction
    trend_pts: Optional[float] = None      # legacy trend strength (used by DITM hard gate)
    macro_hold: bool = False               # macro-context flag (DITM only)

    # Per-symbol levels consumed by strike scorers via strike_context_builder.
    vol_support_1: Optional[float] = None
    vol_support_2: Optional[float] = None
    vol_support_3: Optional[float] = None
    vol_resistance_1: Optional[float] = None
    vol_resistance_2: Optional[float] = None
    vol_resistance_3: Optional[float] = None


@dataclass(frozen=True)
class SymbolMetrics:
    """
    Render-only per-symbol metadata.

    Fields here are NOT read by any scorer; they exist so a screener's
    `result_factory` can populate its concrete result dataclass (BB bands,
    sma_ratio, hv_sigma, iv_percentile, etc.) without bloating `Indicators`.
    The runner threads `SymbolMetrics` through `ExpirationContext`.
    """

    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    sma_ratio: Optional[float] = None      # SMA50 / SMA200
    hv_sigma: Optional[float] = None       # 30d log-return volatility (annualised)
    iv_percentile: Optional[float] = None
    earnings_date: Optional[str] = None    # ISO YYYY-MM-DD; per-expiration view available on ctx


@dataclass(frozen=True)
class StrikeContext:
    """
    Per-strike inputs to the strike scorer.

    Union of every field the three strike scorers consume. Optional fields
    default to None — a CSP scorer leaves DITM-only fields unset and vice
    versa.
    """

    # Common
    delta: float
    strike: float
    current_price: float
    bid_ask_spread_pct: Optional[float]
    open_interest: int
    volume: int
    market_open: bool
    iv_used: float
    dte: int

    # CSP / CC pricing
    credit: Optional[float] = None              # premium (mid)

    # CSP supports / CC resistances (3 levels each, screener picks one)
    vol_support_1: Optional[float] = None
    vol_support_2: Optional[float] = None
    vol_support_3: Optional[float] = None
    vol_resistance_1: Optional[float] = None
    vol_resistance_2: Optional[float] = None
    vol_resistance_3: Optional[float] = None

    # DITM-only (long-call mechanics)
    mid: Optional[float] = None
    extrinsic_pct_of_strike_frac: Optional[float] = None
    theta_annualized_pct: Optional[float] = None
    iv_percentile: Optional[float] = None


# --- Hard gate result ------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    """Outcome of a hard gate. `passed=False` short-circuits the env score
    to 0 with the supplied `reason` recorded in the env detail string."""

    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class StrikeBuildInputs:
    """
    Typed payload handed to `strike_context_builder`.

    Replaces the previous untyped `dict` payload. Holds the per-strike
    candidate plus the per-symbol context the builder needs to assemble
    a `StrikeContext`. Frozen so builders cannot mutate runner-side state.

    `candidate` is intentionally typed `Any` to avoid a `types.py` →
    `runner.py` import cycle; the runtime type is `runner.Candidate`.
    """

    candidate: Any
    current_price: float
    hv_sigma: float
    chain_df: Any
    market_open: bool
    rf_rate: float
    T: float                  # dte / 365.0


# --- Generic result base classes -------------------------------------------

@dataclass
class BaseStrikeResult:
    """Minimal fields every strike result has. Concrete dataclasses
    (`CspStrikeResult`, `CcStrikeResult`, `DitmStrikeResult`) inherit and add
    screener-specific fields; the runner only touches these common fields.

    Note: `is_best` is the only field the runner mutates after construction
    (post-sort). The class is intentionally NOT frozen for that reason."""

    strike: float
    delta: float
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str = ""
    strike_detail: str = ""
    is_best: bool = False


@dataclass
class BaseScreenerResult:
    """Minimal fields every per-symbol result has."""

    symbol: str
    price: float
    dte: int
    expiration: str
    best_score: float = 0.0


# --- Callable type aliases -------------------------------------------------

# (spot, strike, T_years, sigma, rate) -> delta in [-1, 1]
DeltaFn = Callable[[float, float, float, float, float], float]

# (symbol, dte_min, dte_max) -> list of expiration chains; the actual return
# shape is `list[dict]` matching options_service. Kept loose to avoid a
# premature contract here.
ChainFetcher = Callable[[str, int, int], list[dict]]

# (current_price, strike) -> True if strike passes screener-specific filter
# (e.g. OTM puts: strike < price * 1.02; ITM calls: strike < price).
StrikeFilter = Callable[[float, float], bool]

# (Indicators) -> (env_score 0-100, detail string). Receives the union
# bundle; concrete scorer extracts only the fields it needs.
EnvScorer = Callable[[Indicators], tuple[float, str]]

# (StrikeContext) -> (strike_score 0-100, detail string, raw_metrics dict).
# `raw_metrics` carries dist_pct / em_buffer_pct / lq_count / roc_annualized
# etc. so the result_factory can stash them on the concrete result.
StrikeScorer = Callable[[StrikeContext], tuple[float, str, dict[str, Any]]]

# (Indicators) -> GateResult. DITM uses these for trend / hv_rank / earnings
# short-circuits. CSP / CC pass `()` (no gates).
HardGate = Callable[[Indicators], GateResult]

# (symbol, raw_ohlc_df, indicators_in_progress) -> Indicators (mutated copy).
# DITM uses these for macro_context, weekly_rsi, ret_200d enrichment.
PreProcessor = Callable[[str, Any, Indicators], Indicators]

# (StrikeBundle) -> sort key used to pick the best strike.
# CSP / CC: roc_annualized (descending). DITM: -|delta - ideal_delta|.
# Typed as Callable[[Any], float] because the bundle type lives in `runner`
# and importing it here would create a cycle. Concrete tie-breakers should
# read `bundle.candidate.delta` / `bundle.strike_raw["roc_annualized"]`.
TieBreakKey = Callable[[Any], float]

# Builds the concrete strike-result dataclass from runner-side bundle.
ResultFactory = Callable[..., Any]

# (symbol[, period]) -> OHLC DataFrame. Per-screener so test monkeypatches on
# `services.{csp,cc,ditm}_service.get_ohlc` keep working unchanged.
OhlcFetcher = Callable[..., Any]

# (symbol, df, current_price) -> (Indicators, SymbolMetrics). Single factory
# computes both the env-input bundle and the render-only metrics in one
# pass over the OHLC frame.
SymbolFactory = Callable[[str, Any, float], tuple["Indicators", "SymbolMetrics"]]

# (chain_df, strike) -> implied_vol. Indirected through ScreenerConfig so
# tests can monkey-patch each screener's IV lookup independently.
IvLookup = Callable[[Any, float], float]

# (StrikeBuildInputs, indicators) -> StrikeContext. The screener decides
# which fields of the union StrikeContext bundle to populate.
StrikeContextBuilder = Callable[["StrikeBuildInputs", "Indicators"], "StrikeContext"]


__all__ = [
    "BaseScreenerResult",
    "BaseStrikeResult",
    "ChainFetcher",
    "DeltaFn",
    "Direction",
    "EnvScorer",
    "GateResult",
    "HardGate",
    "Indicators",
    "IvLookup",
    "OhlcFetcher",
    "PreProcessor",
    "ResultFactory",
    "StrikeBuildInputs",
    "StrikeContext",
    "StrikeContextBuilder",
    "StrikeFilter",
    "StrikeScorer",
    "SymbolFactory",
    "SymbolMetrics",
    "TieBreakKey",
]
