"""
Swing screener orchestration.

Pipeline (per symbol):
  1. fetch 1y OHLC + SPY OHLC
  2. compute features (indicators + classifier features)
  3. classify best setup
  4. build risk plan (entry/stop/target/RR)
  5. apply HARD GATES (RR ≥ 2.5, setup_score ≥ 40)
  6. compute composite swing score
  7. return SwingResult

Universe scans use a thread pool, mirroring CC. Caching is delegated to
`scan_cache.swing_scan_cache` at the router layer.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from services.data_service import get_ohlc, get_ticker_info
from services.indicators import compute_rsi
from services.scoring.swing import (
    SWING_SCORER_VERSION,
    compute_swing_score,
)
from services.swing.classifier import classify_setup
from services.swing.regime import RegimeState, compute_regime
from services.swing.indicators import (
    compute_ad_line_slope,
    compute_adx,
    compute_atr,
    compute_avg_daily_volume,
    compute_bb_squeeze_percentile,
    compute_consolidation_base,
    compute_ema_alignment,
    compute_fib_retracement_hold,
    compute_gap_fill_candidate,
    compute_higher_lows,
    compute_macd_histogram_inflection,
    compute_rs_vs_spy,
    compute_rsi_divergence,
    compute_stochastic,
    compute_structure_high_reclaim,
    compute_volume_surge,
)
from services.swing.risk import RR_HARD_GATE, build_risk_plan

logger = logging.getLogger(__name__)

# Hard gates
MIN_SETUP_SCORE: float = 40.0
MIN_ADV_USD: float = 5_000_000.0  # $5M average dollar volume
MIN_PRICE: float = 5.0            # avoid sub-$5 chop
EARNINGS_FLAG_DAYS: int = 10
EARNINGS_HARD_BLOCK_DAYS: int = 1            # any setup, ≤ this many days → exclude
EARNINGS_REVERSION_BLOCK_DAYS: int = 7        # reversion + ≤ this many days → exclude


@dataclass(slots=True)
class SwingResult:
    symbol: str
    price: float
    setup_type: str
    setup_score: float
    swing_score: float
    confidence: str
    entry: float
    stop: float
    target: float
    risk_per_share: float
    reward_per_share: float
    rr: float
    hold_min_days: int
    hold_max_days: int
    trigger_kind: str
    extended: bool
    drivers: list[str]
    earnings_date: str | None
    earnings_warning: bool
    # context
    rsi: float | None
    atr14: float | None
    adx: float | None
    rs_vs_spy: float | None
    ema_alignment_score: int | None
    ad_line_slope_pct: float | None
    institutional_ownership_pct: float | None
    bb_squeeze_pct: float | None
    consolidation_days: int | None
    consolidation_range_pct: float | None
    volume_surge_ratio: float | None
    higher_lows: int | None
    macd_inflection: bool
    rsi_divergence: bool
    fib_618_hold: bool
    structure_reclaimed: bool
    setup_scores: dict[str, float] = field(default_factory=dict)
    breakdown: dict[str, float] = field(default_factory=dict)
    multipliers: dict[str, float] = field(default_factory=dict)
    raw_score: float = 0.0
    days_to_earnings: int | None = None
    forced_short_hold: bool = False
    rr_gate: float = 0.0
    regime_label: str = ""
    excluded: bool = False
    exclude_reason: str | None = None


# ---------------------------------------------------------------------------
# Per-symbol data fetchers (isolated for testability)
# ---------------------------------------------------------------------------

def _get_institutional_ownership(symbol: str) -> float | None:
    """Snapshot from yfinance .info heldPercentInstitutions (decimal → percent)."""
    try:
        info = yf.Ticker(symbol).info or {}
        val = info.get("heldPercentInstitutions")
        if val is None:
            return None
        return round(float(val) * 100, 1)
    except Exception as exc:
        logger.debug("inst ownership fetch failed %s: %s", symbol, exc)
        return None


def _get_earnings_date(symbol: str) -> str | None:
    """Return YYYY-MM-DD of next earnings, or None."""
    try:
        cal = yf.Ticker(symbol).calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
            if dates:
                d = dates[0]
                return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        if isinstance(cal, pd.DataFrame) and not cal.empty:
            row = cal.loc["Earnings Date"] if "Earnings Date" in cal.index else None
            if row is not None and len(row) > 0:
                d = row.iloc[0]
                return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
    except Exception as exc:
        logger.debug("earnings fetch failed %s: %s", symbol, exc)
    return None


def _earnings_within_days(earnings_date: str | None, days: int) -> bool:
    if not earnings_date:
        return False
    try:
        edt = datetime.strptime(earnings_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today = datetime.now(tz=timezone.utc)
        return 0 <= (edt - today).days <= days
    except Exception:
        return False


def _days_to_earnings(earnings_date: str | None) -> int | None:
    """Whole-day count to next earnings; None if unknown or in the past."""
    if not earnings_date:
        return None
    try:
        edt = datetime.strptime(earnings_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today = datetime.now(tz=timezone.utc)
        delta = (edt - today).days
        return delta if delta >= 0 else None
    except Exception:
        return None


def _recent_swing_low(df: pd.DataFrame, lookback: int = 10) -> float:
    """Most recent N-bar low, used as a structure-based stop floor."""
    if len(df) < lookback:
        return 0.0
    return float(df["Low"].iloc[-lookback:].min())


# ---------------------------------------------------------------------------
# Per-symbol analysis
# ---------------------------------------------------------------------------

def process_symbol(
    symbol: str,
    spy_df: pd.DataFrame,
    regime: RegimeState | None = None,
    df: pd.DataFrame | None = None,
    bypass_gates: bool = False,
) -> SwingResult:
    """Run the full swing pipeline for one symbol. Never raises.

    When *bypass_gates* is True, strategy filters (price, ADV, setup score,
    disabled setups, earnings blocks, R:R gate) are skipped so that the caller
    always receives a computed result.  Data-quality gates (insufficient
    history, missing ATR) are still enforced because the pipeline cannot run
    without them.
    """
    rr_gate = regime.rr_gate if regime is not None else RR_HARD_GATE
    regime_factor = regime.multiplier if regime is not None else 1.0
    regime_label = regime.regime_label if regime is not None else ""
    disabled = set(regime.disable_setups) if regime is not None else set()
    try:
        if df is None:
            df = get_ohlc(symbol, period="1y")
        if len(df) < 200:
            return _excluded(symbol, "insufficient history")

        price = float(df["Close"].iloc[-1])
        if not bypass_gates and price < MIN_PRICE:
            return _excluded(symbol, f"price < ${MIN_PRICE:.0f}")

        adv = compute_avg_daily_volume(df, period=20)
        adv_usd = adv * price if adv == adv else 0
        if not bypass_gates and adv_usd < MIN_ADV_USD:
            return _excluded(symbol, f"ADV ${adv_usd / 1e6:.1f}M < ${MIN_ADV_USD / 1e6:.0f}M", price=price)

        atr14 = compute_atr(df, period=14)
        if atr14 != atr14 or atr14 <= 0:
            return _excluded(symbol, "no ATR", price=price)

        ema = compute_ema_alignment(df)
        adx = compute_adx(df, period=14)
        bb_pct = compute_bb_squeeze_percentile(df)
        rs = compute_rs_vs_spy(df, spy_df, period=20)
        ad_slope = compute_ad_line_slope(df)
        hl = compute_higher_lows(df)
        stoch = compute_stochastic(df)
        rsi_val = compute_rsi(df)
        rsi_div = compute_rsi_divergence(df)
        macd_inf = compute_macd_histogram_inflection(df)
        base = compute_consolidation_base(df)
        surge = compute_volume_surge(df)
        fib_hold = compute_fib_retracement_hold(df)
        gap = compute_gap_fill_candidate(df)
        reclaim = compute_structure_high_reclaim(df)
        inst_own = _get_institutional_ownership(symbol)
        earnings = _get_earnings_date(symbol)

        features = {
            "price": price,
            "rsi": rsi_val,
            "stochastic": stoch,
            "ema_alignment": ema,
            "adx": adx,
            "bb_squeeze_pct": bb_pct,
            "rs_vs_spy": rs,
            "higher_lows": hl,
            "rsi_divergence": rsi_div,
            "macd_inflection": macd_inf,
            "consolidation_base": base,
            "volume_surge": surge,
            "fib_618_hold": fib_hold,
            "gap_fill": gap,
            "structure_reclaim": reclaim,
        }
        cls = classify_setup(features)
        if not bypass_gates and cls["best_score"] < MIN_SETUP_SCORE:
            return _excluded(
                symbol,
                f"setup score {cls['best_score']:.0f} < {MIN_SETUP_SCORE:.0f}",
                price=price,
                rsi=rsi_val,
                atr14=atr14,
            )

        if not bypass_gates and cls["best_setup"] in disabled:
            return _excluded(
                symbol,
                f"{cls['best_setup']} setup disabled in {regime_label} regime",
                price=price,
                rsi=rsi_val,
                atr14=atr14,
            )

        # Earnings hard blocks (before risk/scoring) ---------------------------
        dte = _days_to_earnings(earnings)
        if not bypass_gates and dte is not None and dte <= EARNINGS_HARD_BLOCK_DAYS:
            return _excluded(
                symbol,
                f"earnings in {dte}d (≤ {EARNINGS_HARD_BLOCK_DAYS}d hard block)",
                price=price,
                rsi=rsi_val,
                atr14=atr14,
            )
        if not bypass_gates and cls["best_setup"] == "reversion" and dte is not None and dte <= EARNINGS_REVERSION_BLOCK_DAYS:
            return _excluded(
                symbol,
                f"reversion + earnings in {dte}d (≤ {EARNINGS_REVERSION_BLOCK_DAYS}d hard block)",
                price=price,
                rsi=rsi_val,
                atr14=atr14,
            )

        plan = build_risk_plan(
            setup=cls["best_setup"],
            current_price=price,
            atr14=atr14,
            recent_swing_low=_recent_swing_low(df),
            features=features,
        )
        if not bypass_gates and (not plan.passes_gate or plan.rr < rr_gate):
            return _excluded(
                symbol,
                f"R:R {plan.rr:.2f} < {rr_gate:.1f} ({regime_label or 'baseline'} gate)",
                price=price,
                rsi=rsi_val,
                atr14=atr14,
            )

        # Hold-window trim if it would cross earnings -------------------------
        hold_min = plan.hold_min_days
        hold_max = plan.hold_max_days
        forced_short_hold = False
        if dte is not None and dte < hold_max:
            new_max = max(1, dte - 1)
            if new_max < hold_min:
                hold_min = new_max
            hold_max = new_max
            forced_short_hold = True

        scored = compute_swing_score(
            rr=plan.rr,
            setup_score=cls["best_score"],
            adx_value=adx.get("adx"),
            ad_line_slope_pct=ad_slope,
            higher_lows=hl,
            institutional_ownership_pct=inst_own,
            regime_factor=regime_factor,
            days_to_earnings=dte,
            extended=plan.extended,
        )

        return SwingResult(
            symbol=symbol,
            price=round(price, 2),
            setup_type=cls["best_setup"],
            setup_score=cls["best_score"],
            swing_score=scored["score"],
            confidence=scored["confidence"],
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            risk_per_share=plan.risk_per_share,
            reward_per_share=plan.reward_per_share,
            rr=plan.rr,
            hold_min_days=hold_min,
            hold_max_days=hold_max,
            trigger_kind=plan.trigger_kind,
            extended=plan.extended,
            drivers=cls["drivers"],
            earnings_date=earnings,
            earnings_warning=_earnings_within_days(earnings, EARNINGS_FLAG_DAYS),
            rsi=round(rsi_val, 2) if rsi_val == rsi_val else None,
            atr14=round(atr14, 2),
            adx=adx.get("adx"),
            rs_vs_spy=rs if rs == rs else None,
            ema_alignment_score=ema.get("score"),
            ad_line_slope_pct=ad_slope if ad_slope == ad_slope else None,
            institutional_ownership_pct=inst_own,
            bb_squeeze_pct=bb_pct if bb_pct == bb_pct else None,
            consolidation_days=base.get("days"),
            consolidation_range_pct=base.get("range_pct") if base.get("range_pct") == base.get("range_pct") else None,
            volume_surge_ratio=surge.get("ratio") if surge.get("ratio") == surge.get("ratio") else None,
            higher_lows=hl,
            macd_inflection=macd_inf,
            rsi_divergence=rsi_div,
            fib_618_hold=fib_hold,
            structure_reclaimed=reclaim.get("reclaimed", False),
            setup_scores=cls["scores"],
            breakdown=scored["breakdown"],
            multipliers=scored["multipliers"],
            raw_score=scored["raw_score"],
            days_to_earnings=dte,
            forced_short_hold=forced_short_hold,
            rr_gate=rr_gate,
            regime_label=regime_label,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("swing process_symbol failed for %s: %s", symbol, exc)
        return _excluded(symbol, f"error: {exc}")


def _excluded(
    symbol: str,
    reason: str,
    *,
    price: float = 0.0,
    rsi: float | None = None,
    atr14: float | None = None,
) -> SwingResult:
    return SwingResult(
        symbol=symbol,
        price=round(price, 2),
        setup_type="",
        setup_score=0.0,
        swing_score=0.0,
        confidence="speculative",
        entry=0.0, stop=0.0, target=0.0,
        risk_per_share=0.0, reward_per_share=0.0, rr=0.0,
        hold_min_days=0, hold_max_days=0,
        trigger_kind="",
        extended=False,
        drivers=[],
        earnings_date=None,
        earnings_warning=False,
        rsi=rsi if rsi is not None and rsi == rsi else None,
        atr14=atr14 if atr14 is not None and atr14 == atr14 else None,
        adx=None, rs_vs_spy=None, ema_alignment_score=None,
        ad_line_slope_pct=None, institutional_ownership_pct=None,
        bb_squeeze_pct=None, consolidation_days=None, consolidation_range_pct=None,
        volume_surge_ratio=None, higher_lows=None,
        macd_inflection=False, rsi_divergence=False, fib_618_hold=False,
        structure_reclaimed=False,
        excluded=True, exclude_reason=reason,
    )


# ---------------------------------------------------------------------------
# Universe scan
# ---------------------------------------------------------------------------

def _fetch_ohlc_safe(symbol: str) -> tuple[str, pd.DataFrame | None]:
    try:
        return symbol, get_ohlc(symbol, period="1y")
    except Exception as exc:  # noqa: BLE001
        logger.debug("OHLC fetch failed for %s: %s", symbol, exc)
        return symbol, None


def run_scan(
    symbols: list[str],
    max_workers: int = 8,
    bypass_gates: bool = False,
) -> tuple[list[dict], RegimeState | None]:
    """
    Scan a universe of symbols. Returns ``(rows, regime)`` where ``rows`` is
    the sorted list of result dicts (qualified candidates first, sorted by
    swing_score desc) and ``regime`` is the RegimeState used for gating
    (or None when SPY fetch fully fails).

    Two-stage pipeline:
      1. Pre-fetch all OHLC in parallel (one yfinance call per symbol).
      2. Compute regime once (uses pre-fetched OHLC for breadth).
      3. Parallel per-symbol scoring with the regime injected.

    When *bypass_gates* is True, strategy filters are skipped so every
    symbol that has sufficient data history is returned.  Use for custom
    symbol requests where the caller explicitly named each ticker.
    Excluded symbols are dropped from the response.

    NOTE: prior to Phase-1 cleanup the regime was written to a module-global
    ``scan_cache.regime_cache`` as a side-effect so the caller could fish it
    out. That singleton leaked one user's regime snapshot into another's
    request and could not be keyed to ``as_of``. It has been removed; the
    regime is returned explicitly.
    """
    try:
        spy_df = get_ohlc("SPY", period="1y")
    except Exception as exc:
        logger.error("SPY fetch failed; using flat baseline: %s", exc)
        spy_df = pd.DataFrame({"Close": [1.0] * 252})

    # Stage 1: pre-fetch OHLC for the whole universe (used by regime + scan)
    ohlc: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sym, df in ex.map(_fetch_ohlc_safe, symbols):
            if df is not None:
                ohlc[sym] = df

    # Stage 2: regime (computed fresh per scan; no shared cache)
    regime = compute_regime(spy_df=spy_df, universe_ohlc=ohlc)
    logger.info(
        "swing scan regime=%s rr_gate=%.1f multiplier=%.2f breadth=%.0f%%",
        regime.regime_label, regime.rr_gate, regime.multiplier, regime.breadth_pct,
    )

    # Stage 3: per-symbol scoring
    qualified: list[SwingResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(process_symbol, sym, spy_df, regime, ohlc.get(sym), bypass_gates): sym
            for sym in symbols
        }
        for fut in as_completed(futures):
            res = fut.result()
            if not res.excluded:
                qualified.append(res)
    qualified.sort(key=lambda r: r.swing_score, reverse=True)
    return [asdict(r) for r in qualified], regime


def get_version() -> str:
    return SWING_SCORER_VERSION
