"""
`ScreenerConfig` — the parameter bundle that turns the generic Phase 3 runner
into a CSP, CC, or DITM screener.

Every field is a value or a callable — **no logic** lives in this module. The
runner reads the config; concrete screeners populate it. Phase 4 will add
three module-level constants (`CSP_CONFIG`, `CC_CONFIG`, `DITM_CONFIG`) that
fully specify each screener.

Why so many callables? See the divergence map in plan-screener-refactor.md:
the three screeners differ on direction, chain endpoint, delta sign, strike
predicate, capital basis, blend weights, env scorer signature, strike scorer
signature, hard gates, and tie-break metric. Anything less granular collapses
back into per-screener `if direction == ...` branches, which is exactly what
this refactor exists to eliminate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import (
    ChainFetcher,
    DeltaFn,
    Direction,
    EnvScorer,
    HardGate,
    IvLookup,
    OhlcFetcher,
    PreProcessor,
    ResultFactory,
    StrikeContextBuilder,
    StrikeFilter,
    StrikeScorer,
    SymbolFactory,
    TieBreakKey,
)


@dataclass(frozen=True)
class ScreenerConfig:
    """
    Immutable configuration for one screener variant.

    The runner consumes a `ScreenerConfig` plus a symbol + DTE bounds and
    returns a per-symbol result. All variant-specific behavior is dispatched
    through these fields; the runner contains no `if direction == ...`.
    """

    # --- Identity ----------------------------------------------------------
    name: str                              # 'csp', 'cc', 'ditm' (used in logs)
    direction: Direction                   # 'short_put' | 'short_call' | 'long_call'

    # --- External-data adapters -------------------------------------------
    chain_fetcher: ChainFetcher            # puts / calls / itm-calls endpoint
    delta_fn: DeltaFn                      # BS put / call delta
    ohlc_fetcher: OhlcFetcher              # data_service.get_ohlc (per-screener for test patches)
    iv_lookup: IvLookup                    # options_service.get_implied_volatility (per-screener for test patches)

    # --- Strike selection -------------------------------------------------
    strike_filter: StrikeFilter            # OTM puts / OTM calls / ITM calls
    delta_range: tuple[float, float]       # primary delta band
    ideal_delta: float                     # fallback target when band is empty

    # --- Open-interest band for chain_median_oi ---------------------------
    oi_delta_band: tuple[float, float]     # (-0.40, -0.10) | (0.10, 0.40) | (0.60, 0.95)

    # --- Indicator + strike-context assembly -----------------------------
    symbol_factory: SymbolFactory          # (symbol, df, current_price) -> (Indicators, SymbolMetrics)
    strike_context_builder: StrikeContextBuilder  # (StrikeBuildInputs, Indicators) -> StrikeContext

    # --- Scoring ----------------------------------------------------------
    env_scorer: EnvScorer
    strike_scorer: StrikeScorer
    final_blend: tuple[float, float]       # (env_weight, strike_weight); must sum to ~1.0

    # --- Variant hooks ----------------------------------------------------
    pre_processors: tuple[PreProcessor, ...] = field(default_factory=tuple)
    """Run on the indicator bundle after base computation, before scoring.
    DITM uses these for macro_context / weekly_rsi / ret_200d enrichment.
    CSP/CC supply ()."""

    hard_gates: tuple[HardGate, ...] = field(default_factory=tuple)
    """Short-circuit gates applied to `Indicators`. If any returns
    `passed=False`, env_score is forced to 0 with the gate's reason recorded.
    DITM uses these (trend / hv_rank / earnings); CSP/CC supply ()."""

    tie_break_key: Optional[TieBreakKey] = None
    """Sort key for picking the 'best' strike. None defaults to descending
    final_score. CSP/CC use roc_annualized; DITM uses delta-proximity to
    `ideal_delta`."""

    # --- Result construction ---------------------------------------------
    result_factory: Optional[ResultFactory] = None
    """Builds the screener-specific strike + result dataclasses from the
    runner's intermediate bundle. Set in Phase 3+; None here means the
    runner should raise NotImplementedError. Kept Optional so Phase 2 can
    instantiate config sketches without a factory."""

    # --- Diagnostic --------------------------------------------------------
    def describe(self) -> str:
        """One-line summary used in logs."""
        return (
            f"ScreenerConfig(name={self.name}, direction={self.direction}, "
            f"delta_range={self.delta_range}, blend={self.final_blend})"
        )

    def __post_init__(self) -> None:
        env_w, strike_w = self.final_blend
        total = env_w + strike_w
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"final_blend weights must sum to ~1.0, got {env_w} + {strike_w} = {total}"
            )
        if env_w < 0 or strike_w < 0:
            raise ValueError(f"final_blend weights must be non-negative: {self.final_blend}")


__all__ = ["ScreenerConfig"]
