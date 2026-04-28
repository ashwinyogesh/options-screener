"""
Generic per-symbol screener runner.

Replaces the duplicated `process_symbol` orchestration in `csp_service`,
`cc_service`, and `ditm_service` with one parameterised function. Variant
behaviour is dispatched through `ScreenerConfig` callables; the runner
contains no `if direction == ...` branches.

This module is the keystone of Phase 3. CSP migrates first (Phase 3); CC and
DITM follow in Phase 4.

Behaviour preservation: the runner is a literal extraction of the CSP /
CC / DITM `process_symbol` flow. Where the legacy code did something
screener-specific (chain endpoint, delta sign, support vs resistance, etc.)
that branch is now a config callable. No scoring constant changes.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
import pytz

from .config import ScreenerConfig
from .types import (
    GateResult,
    Indicators,
    StrikeBuildInputs,
    StrikeContext,
    SymbolMetrics,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public error type
# ---------------------------------------------------------------------------

@dataclass
class ScreenerError:
    """Generic per-symbol failure. Concrete services translate this to their
    historic error type (`CspError`, `CcError`, `DitmError`) at the wrapper
    boundary if they need shape compatibility."""

    symbol: str
    reason: str


# ---------------------------------------------------------------------------
# Internal bundles
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """Per-strike candidate after raw chain extraction.

    Field set is the union of what CSP/CC/DITM need; populated by the runner's
    `_extract_candidate` helper from a chain row + delta + IV. Optional fields
    are filled by config-supplied logic (e.g. DITM-only `mid`/`extrinsic`)."""

    strike: float
    delta: float
    iv_used: float                  # sigma actually used for delta (may be HV fallback)
    iv_raw: float                   # raw chain IV (may be NaN)
    iv_stale: bool                  # True when raw IV NaN/<=0.01
    iv_fallback: bool               # True when iv_used is hv_sigma (synonym of iv_stale today)
    iv_hv_ratio: Optional[float]    # iv_raw / hv_sigma when both present
    bid: float
    ask: float
    last: float
    premium: Optional[float]        # mid price; None when no usable premium
    stale_premium: bool             # True when fell back to lastPrice
    open_interest: int
    volume: int


@dataclass
class StrikeBundle:
    """Runner-side per-strike result, threaded into `result_factory`."""

    candidate: Candidate
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str
    strike_detail: str
    strike_raw: dict
    bid_ask_spread_pct: Optional[float]
    is_best: bool = False


@dataclass
class ExpirationContext:
    """Per-expiration context handed to `result_factory` so it can build the
    screener-specific result dataclass."""

    symbol: str
    current_price: float
    df: Any
    market_open: bool
    expiration: str
    dte: int
    earnings_date: Optional[str]
    earnings_within_dte: bool
    chain_median_oi: float
    indicators: Indicators
    metrics: SymbolMetrics
    rf_rate: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_market_open() -> bool:
    """US equity-market open detection (ET). Mirrors the legacy services'
    inline block bit-for-bit so the `_market_open` flag passed to strike
    scorers is unchanged."""
    try:
        et = pytz.timezone("America/New_York")
        now = datetime.now(et)
        weekday = now.weekday()
        return (
            weekday < 5
            and now.hour * 60 + now.minute >= 9 * 60 + 30
            and now.hour * 60 + now.minute < 16 * 60
        )
    except Exception:
        return False


def _compute_hv_sigma(df: Any) -> float:
    """30-day annualised log-return standard deviation. Falls back to 0.25
    when fewer than 30 daily returns exist. Identical to the legacy inline
    block in csp_service / cc_service / ditm_service."""
    log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
    if len(log_ret) >= 30:
        return float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252))
    return 0.25


def _earnings_within_dte(earnings_date: Optional[str], dte: int) -> bool:
    if not earnings_date:
        return False
    try:
        ed = date.fromisoformat(earnings_date)
        days_to = (ed - date.today()).days
        return 0 <= days_to <= dte
    except ValueError:
        return False


def _row_float(row: pd.DataFrame, col: str) -> float:
    """Extract a float from a single-row DataFrame, treating NaN as 0.0.
    Matches legacy `__import__('pandas').isna(...)` guard."""
    val = row[col].iloc[0]
    return 0.0 if pd.isna(val) else float(val)


def _row_int(row: pd.DataFrame, col: str) -> int:
    val = row[col].iloc[0]
    return 0 if pd.isna(val) else int(val)


def _extract_candidate(
    chain_df: pd.DataFrame,
    strike: float,
    current_price: float,
    T: float,
    rf_rate: float,
    hv_sigma: float,
    delta_fn: Any,
    iv_lookup: Any,
) -> Optional[Candidate]:
    """
    Extract one Candidate from a chain row. Returns None when the row is
    missing or the strike has no usable premium AND we're past the
    has-premium check (premium=None Candidates are kept for OI aggregation).

    Mirrors the legacy `candidates`/`_delta_range_ois_all` extraction loop
    from csp_service / cc_service / ditm_service. The runner builds every
    Candidate it can; the caller decides whether to keep premium-less ones.
    """
    row = chain_df[chain_df["strike"] == strike]
    if row.empty:
        return None

    bid = _row_float(row, "bid")
    ask = _row_float(row, "ask")
    last = _row_float(row, "lastPrice")
    oi = _row_int(row, "openInterest")
    vol = _row_int(row, "volume")

    iv_raw = iv_lookup(chain_df, strike)
    iv_stale = math.isnan(iv_raw) or iv_raw <= 0.01

    iv_hv_ratio: Optional[float] = None
    if iv_stale:
        iv_used = hv_sigma
        iv_fallback = True
    else:
        iv_used = iv_raw
        iv_fallback = False
        if hv_sigma > 0:
            iv_hv_ratio = round(iv_raw / hv_sigma, 4)

    delta = delta_fn(current_price, strike, rf_rate, T, iv_used)

    if bid > 0 and ask > 0:
        premium: Optional[float] = round((bid + ask) / 2.0, 4)
        stale_premium = False
    elif last > 0:
        premium = round(last, 4)
        stale_premium = True
    else:
        premium = None
        stale_premium = False

    return Candidate(
        strike=strike,
        delta=delta,
        iv_used=iv_used,
        iv_raw=iv_raw,
        iv_stale=iv_stale,
        iv_fallback=iv_fallback,
        iv_hv_ratio=iv_hv_ratio,
        bid=bid,
        ask=ask,
        last=last,
        premium=premium,
        stale_premium=stale_premium,
        open_interest=oi,
        volume=vol,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    symbol: str,
    config: ScreenerConfig,
    *,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
) -> tuple[list[Any], Optional[ScreenerError]]:
    """
    Process one symbol end-to-end. Returns (results, None) on success or
    ([], error) on failure.

    Args:
        symbol: ticker (case-insensitive; trimmed/uppercased internally).
        config: per-screener `ScreenerConfig`.
        min_dte / max_dte: expiration window passed to `chain_fetcher`.
        rf_rate: risk-free rate fed into `delta_fn`.

    External adapters (OHLC fetcher, chain fetcher, IV lookup) are read
    from `config` rather than runner kwargs so each screener owns its own
    monkeypatch surface.
    """
    sym = symbol.strip().upper()
    try:
        df = config.ohlc_fetcher(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])

        # Common per-symbol prep
        market_open = _detect_market_open()

        # Build base indicators + render-only metrics in one pass.
        base_indicators, metrics = config.symbol_factory(sym, df, current_price)

        # Pre-processors (DITM macro_context, weekly_rsi, etc.). CSP/CC pass ().
        for pre in config.pre_processors:
            base_indicators = pre(sym, df, base_indicators)

        all_exps = config.chain_fetcher(sym, min_dte, max_dte)

        results: list[Any] = []
        for opts in all_exps:
            try:
                row = _process_expiration(
                    config=config,
                    sym=sym,
                    df=df,
                    current_price=current_price,
                    market_open=market_open,
                    base_indicators=base_indicators,
                    metrics=metrics,
                    opts=opts,
                    rf_rate=rf_rate,
                )
                if row is not None:
                    results.append(row)
            except Exception as exc:
                logger.debug("Skipping expiration %s for %s: %s", opts.get("expiration"), sym, exc)
                continue

        if not results:
            return [], ScreenerError(symbol=sym, reason="No valid expirations processed")
        return results, None

    except Exception as exc:
        logger.warning("Failed to process '%s': %s", sym, exc)
        return [], ScreenerError(symbol=sym, reason=str(exc))


def _process_expiration(
    *,
    config: ScreenerConfig,
    sym: str,
    df: Any,
    current_price: float,
    market_open: bool,
    base_indicators: Indicators,
    metrics: SymbolMetrics,
    opts: dict,
    rf_rate: float,
) -> Optional[Any]:
    """One expiration's worth of work. Returns the result_factory output or
    None when no usable strikes survive."""
    dte = opts["dte"]
    expiration = opts["expiration"]
    earnings_date = opts.get("earnings_date")
    earnings_within = _earnings_within_dte(earnings_date, dte)

    # Chain DataFrame: legacy services use 'puts_df' for CSP and 'calls_df'
    # for CC/DITM. We accept either key.
    chain_df = opts.get("puts_df", opts.get("calls_df"))
    if chain_df is None:
        return None

    T = dte / 365.0
    hv_sigma = metrics.hv_sigma or 0.0

    # Strike ordering: legacy sorts descending (ATM-first for OTM puts) for
    # CSP and ascending for CC/DITM. We sort by direction: puts → desc, calls
    # (short or long) → asc. The strike_filter then prunes to the screener's
    # OTM/ITM region.
    strikes_unique = chain_df["strike"].unique()
    if config.direction == "short_put":
        strikes_sorted = sorted(strikes_unique, reverse=True)
    else:
        strikes_sorted = sorted(strikes_unique)

    filtered = [s for s in strikes_sorted if config.strike_filter(current_price, s)]

    # Build candidates and OI-band aggregation in one pass.
    candidates: list[Candidate] = []
    oi_band: list[int] = []
    oi_lo, oi_hi = config.oi_delta_band
    oi_lo_abs, oi_hi_abs = abs(oi_lo), abs(oi_hi)
    if oi_lo_abs > oi_hi_abs:
        oi_lo_abs, oi_hi_abs = oi_hi_abs, oi_lo_abs

    for sp in filtered:
        try:
            cand = _extract_candidate(
                chain_df, sp, current_price, T, rf_rate, hv_sigma,
                config.delta_fn, config.iv_lookup,
            )
            if cand is None:
                continue
            # OI aggregation uses absolute delta range; matches legacy
            # `0.1 < abs(d) < 0.4` style guard but parameterised.
            abs_d = abs(cand.delta)
            if oi_lo_abs < abs_d < oi_hi_abs:
                oi_band.append(cand.open_interest)
            if cand.premium is not None:
                candidates.append(cand)
        except Exception:
            continue

    chain_median_oi = float(np.median(oi_band)) if oi_band else 0.0

    # Primary delta-range filter; fallback to nearest-ideal (top 5).
    lo, hi = config.delta_range
    in_range = [c for c in candidates if lo <= c.delta <= hi]
    if not in_range and candidates:
        in_range = sorted(candidates, key=lambda c: abs(c.delta - config.ideal_delta))[:5]
    if not in_range:
        return None

    # Per-expiration Indicators: layer on dte / earnings / chain_median_oi.
    days_to_earnings = base_indicators.days_to_earnings
    if earnings_date:
        try:
            days_to_earnings = (date.fromisoformat(earnings_date) - date.today()).days
        except ValueError:
            pass

    exp_indicators = replace(
        base_indicators,
        dte=dte,
        earnings_within_dte=earnings_within,
        chain_median_oi=chain_median_oi,
        days_to_earnings=days_to_earnings,
    )

    # Hard gates (DITM uses these; CSP/CC pass empty tuple).
    gate_failure: Optional[GateResult] = None
    for gate in config.hard_gates:
        gr = gate(exp_indicators)
        if not gr.passed:
            gate_failure = gr
            break

    # Score every in-range candidate.
    bundles: list[StrikeBundle] = []
    env_w, strike_w = config.final_blend
    for cand in in_range:
        try:
            bundle = _score_candidate(
                config=config,
                cand=cand,
                exp_indicators=exp_indicators,
                market_open=market_open,
                current_price=current_price,
                hv_sigma=hv_sigma,
                gate_failure=gate_failure,
                env_w=env_w,
                strike_w=strike_w,
                chain_df=chain_df,
                rf_rate=rf_rate,
                T=T,
            )
            bundles.append(bundle)
        except Exception:
            continue

    if not bundles:
        return None

    # Best strike: max(final_score, tie_break_key). Tie-break defaults to 0.
    tb = config.tie_break_key
    if tb is None:
        best_idx = max(range(len(bundles)), key=lambda i: bundles[i].final_score)
    else:
        best_idx = max(
            range(len(bundles)),
            key=lambda i: (bundles[i].final_score, tb(bundles[i])),
        )
    bundles[best_idx].is_best = True

    ctx = ExpirationContext(
        symbol=sym,
        current_price=current_price,
        df=df,
        market_open=market_open,
        expiration=expiration,
        dte=dte,
        earnings_date=earnings_date,
        earnings_within_dte=earnings_within,
        chain_median_oi=chain_median_oi,
        indicators=exp_indicators,
        metrics=metrics,
        rf_rate=rf_rate,
    )

    if config.result_factory is None:
        raise RuntimeError(
            f"ScreenerConfig(name={config.name}) has no result_factory; cannot build result row."
        )
    return config.result_factory(ctx, bundles)


def _score_candidate(
    *,
    config: ScreenerConfig,
    cand: Candidate,
    exp_indicators: Indicators,
    market_open: bool,
    current_price: float,
    hv_sigma: float,
    gate_failure: Optional[GateResult],
    env_w: float,
    strike_w: float,
    chain_df: Any,
    rf_rate: float,
    T: float,
) -> StrikeBundle:
    """Score one candidate end-to-end (env + strike + blend)."""
    # Layer per-candidate IV fields onto Indicators for the env scorer.
    cand_indicators = replace(
        exp_indicators,
        iv_hv_ratio=cand.iv_hv_ratio,
        iv_stale=cand.iv_stale,
    )

    if gate_failure is not None:
        env_score: float = 0.0
        env_detail = f"Gate:{gate_failure.reason}"
    else:
        env_score, env_detail = config.env_scorer(cand_indicators)

    # Strike context — screener-specific (vol_supports vs resistances vs DITM
    # extrinsic / theta). Builder pulls per-symbol levels from cand_indicators.
    inputs = StrikeBuildInputs(
        candidate=cand,
        current_price=current_price,
        hv_sigma=hv_sigma,
        chain_df=chain_df,
        market_open=market_open,
        rf_rate=rf_rate,
        T=T,
    )
    strike_ctx: StrikeContext = config.strike_context_builder(inputs, cand_indicators)

    strike_score, strike_detail, strike_raw = config.strike_scorer(strike_ctx)

    final_score = round(env_w * env_score + strike_w * strike_score, 1)
    return StrikeBundle(
        candidate=cand,
        env_score=env_score,
        strike_score=strike_score,
        final_score=final_score,
        env_detail=env_detail,
        strike_detail=strike_detail,
        strike_raw=strike_raw,
        bid_ask_spread_pct=strike_ctx.bid_ask_spread_pct,
    )


__all__ = [
    "Candidate",
    "ExpirationContext",
    "ScreenerError",
    "StrikeBundle",
    "run",
]
