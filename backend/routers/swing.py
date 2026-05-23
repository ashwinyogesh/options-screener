"""
GET  /api/screener/swing/scan   — universe scan, returns ranked SwingResults
POST /api/screener/swing        — custom symbol list (≤ 20)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from services.scan_cache import swing_scan_cache
from services.scoring.swing import SWING_SCORER_VERSION
from services.scoring.swing_lasso import SWING_LASSO_VERSION
from services.screener.result_store import ScreenerStoreEmpty, get_swing_results
from services.swing.regime import RegimeState, compute_regime
from services.swing_insight_service import get_batch_commentary
from services.swing_service import process_symbol, run_scan
from services.universe import UNIVERSES, get_universe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["swing"])

_MAX_SYMBOLS = 20


class SwingRequest(BaseModel):
    symbols: list[str]
    bypass_gates: bool = True

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols list must not be empty")
        if len(v) > _MAX_SYMBOLS:
            raise ValueError(f"Maximum {_MAX_SYMBOLS} symbols per request")
        cleaned = [s.strip().upper() for s in v if s.strip()]
        if not cleaned:
            raise ValueError("symbols list contains no valid entries")
        for sym in cleaned:
            if len(sym) > 10 or not sym.isalnum():
                raise ValueError(f"Invalid symbol: '{sym}'")
        return cleaned


class SwingResultOut(BaseModel):
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
    trigger_kind: str = ""
    extended: bool = False
    drivers: list[str]
    earnings_date: str | None = None
    earnings_warning: bool = False
    rsi: float | None = None
    atr14: float | None = None
    adx: float | None = None
    rs_vs_spy: float | None = None
    ema_alignment_score: int | None = None
    ad_line_slope_pct: float | None = None
    institutional_ownership_pct: float | None = None
    bb_squeeze_pct: float | None = None
    consolidation_days: int | None = None
    consolidation_range_pct: float | None = None
    volume_surge_ratio: float | None = None
    higher_lows: int | None = None
    macd_inflection: bool = False
    rsi_divergence: bool = False
    fib_618_hold: bool = False
    structure_reclaimed: bool = False
    macd_hist_val: float | None = None
    bb_position_val: float | None = None
    setup_scores: dict[str, float] = {}
    breakdown: dict[str, float] = {}
    multipliers: dict[str, float] = {}
    raw_score: float = 0.0
    days_to_earnings: int | None = None
    forced_short_hold: bool = False
    rr_gate: float = 0.0
    regime_label: str = ""
    narrative: str | None = None
    risk_note: str | None = None
    # --- v3 Lasso calibrated probability scorer ---
    swing_score_v2: float = 0.0
    swing_score_v3: int = 0
    p_target: float | None = None
    lasso_confidence: str = "speculative"
    lasso_top_features: list[dict] = []
    lasso_missing_features: list[str] = []
    # --- composite: 30% v3.0 rank + 70% Lasso rank, scaled 0-100 ---
    composite_score: int = 0


class SwingResponse(BaseModel):
    results: list[SwingResultOut]
    scoring_version: str = SWING_SCORER_VERSION
    scoring_version_v3: str = SWING_LASSO_VERSION
    regime: RegimeOut | None = None
    last_updated_at: Optional[str] = None


class RegimeOut(BaseModel):
    index_trend: str
    vol_regime: str
    breadth_pct: float
    risk_appetite: float
    risk_on_score: float
    regime_label: str
    rr_gate: float
    multiplier: float
    disable_setups: list[str]
    drivers: list[str]
    degraded: bool
    spy_close: float
    spy_ema21: float
    spy_ema50: float
    vix: float
    vix_percentile: float


def _regime_to_out(r: RegimeState) -> RegimeOut:
    return RegimeOut(
        index_trend=r.index_trend,
        vol_regime=r.vol_regime,
        breadth_pct=r.breadth_pct,
        risk_appetite=r.risk_appetite,
        risk_on_score=r.risk_on_score,
        regime_label=r.regime_label,
        rr_gate=r.rr_gate,
        multiplier=r.multiplier,
        disable_setups=r.disable_setups,
        drivers=r.drivers,
        degraded=r.degraded,
        spy_close=r.spy_close,
        spy_ema21=r.spy_ema21,
        spy_ema50=r.spy_ema50,
        vix=r.vix,
        vix_percentile=r.vix_percentile,
    )


def _regime_from_dict(d: dict[str, Any]) -> RegimeOut:
    """Reconstruct a RegimeOut from a stored regime dict."""
    return RegimeOut(
        index_trend=d.get("index_trend", ""),
        vol_regime=d.get("vol_regime", ""),
        breadth_pct=d.get("breadth_pct", 50.0),
        risk_appetite=d.get("risk_appetite", 0.5),
        risk_on_score=d.get("risk_on_score", 50.0),
        regime_label=d.get("regime_label", "neutral"),
        rr_gate=d.get("rr_gate", 2.5),
        multiplier=d.get("multiplier", 1.0),
        disable_setups=d.get("disable_setups", []),
        drivers=d.get("drivers", []),
        degraded=d.get("degraded", False),
        spy_close=d.get("spy_close", 0.0),
        spy_ema21=d.get("spy_ema21", 0.0),
        spy_ema50=d.get("spy_ema50", 0.0),
        vix=d.get("vix", 0.0),
        vix_percentile=d.get("vix_percentile", 50.0),
    )


def _pct_rank(vals: list[float]) -> list[float]:
    """Percentile ranks in [0, 1] with average-rank tie-breaking."""
    n = len(vals)
    if n <= 1:
        return [1.0] * n
    order = sorted(range(n), key=lambda i: vals[i])
    ranks: list[float] = [0.0] * n
    i = 0
    while i < n:
        j = i + 1
        while j < n and vals[order[j]] == vals[order[i]]:
            j += 1
        avg = (i + j - 1) / 2.0 / (n - 1)
        for k in range(i, j):
            ranks[order[k]] = avg
        i = j
    return ranks


def _inject_composite(rows: list[dict]) -> None:
    """Add composite_score key to each raw result dict (before SwingResultOut construction).

    composite = 30% v3.0 additive rank + 70% Lasso P(target) rank, scaled 0-100.
    Rank-normalisation removes the Lasso's score-distribution skew (53% of
    trades cluster at P≈32-35%) and combines both models' discriminative power.
    IC on 3,366-trade backtest: rho = +0.353 vs +0.250 (v3.0) and +0.344 (Lasso).

    For n < 10 results (custom scans, near-empty universes) percentile ranks are
    meaningless — a single stock is trivially rank-1 → composite=100 regardless of
    quality.  Fall back to absolute weighted average in that case.
    """
    n = len(rows)
    if n == 0:
        return
    v3_vals    = [float(r.get("swing_score") or 0)          for r in rows]
    lasso_vals = [float(r.get("p_target") or 0) * 100       for r in rows]
    if n >= 10:
        v3_r    = _pct_rank(v3_vals)
        lasso_r = _pct_rank(lasso_vals)
        for i, r in enumerate(rows):
            r["composite_score"] = round((0.30 * v3_r[i] + 0.70 * lasso_r[i]) * 100)
    else:
        # Absolute weighted average: swing_score and p_target*100 are both 0–100.
        for i, r in enumerate(rows):
            r["composite_score"] = round(0.30 * v3_vals[i] + 0.70 * lasso_vals[i])


def _compute_swing_regime(spy_df=None, universe_ohlc=None) -> RegimeState:
    """Compute the market regime for the swing screener.

    Previously memoized via a process-global ``regime_cache`` singleton.
    That cache leaked one user's regime snapshot into another's request
    (one-key cache, no scoping) and could not be keyed to ``as_of``;
    Phase-1 cleanup removed it. The regime computation is cheap relative
    to the OHLC fetches already performed, so per-call computation is
    acceptable.
    """
    return compute_regime(spy_df=spy_df, universe_ohlc=universe_ohlc)


@router.get("/swing/regime", response_model=RegimeOut)
async def get_swing_regime() -> RegimeOut:
    """Return the current global market regime used by the swing screener."""
    from services.data_service import get_ohlc as _get_ohlc

    def _build() -> RegimeState:
        try:
            spy_df = _get_ohlc("SPY", period="1y")
        except Exception as exc:  # noqa: BLE001
            logger.warning("regime endpoint: SPY fetch failed: %s", exc)
            spy_df = None
        # No universe OHLC available for the standalone endpoint — breadth degrades to neutral.
        return _compute_swing_regime(spy_df=spy_df, universe_ohlc=None)

    state = await asyncio.to_thread(_build)
    return _regime_to_out(state)


@router.get("/swing/scan", response_model=SwingResponse)
async def run_swing_scan(
    universe: str = Query(
        default="swing_eligible",
        description=f"Universe key: one of {sorted(UNIVERSES)}",
    ),
) -> SwingResponse:
    """
    Returns precomputed swing universe scan results. All stored symbols are
    returned (no server-side quality filtering); the frontend applies score,
    setup-type, R:R, and confidence filters. Results are updated every
    15 min during market hours and every 4 h outside market hours by a
    background Container Apps Job (ADR-0025). Custom symbol lists use POST /swing.
    """
    universe_key, symbols = get_universe(universe)

    try:
        rows, regime_dict, last_updated_at, _oldest_age = await asyncio.to_thread(
            get_swing_results, symbols
        )
    except ScreenerStoreEmpty as exc:
        logger.warning("Swing precomputed store empty: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Precomputed swing results are not yet available. "
                "The background worker is populating the store — retry in a few minutes."
            ),
        ) from exc

    _inject_composite(rows)
    results = [SwingResultOut(**r) for r in rows]
    regime_out = _regime_from_dict(regime_dict) if regime_dict else None

    logger.info(
        "Swing scan (precomputed): universe=%s returning %d rows, last_updated=%s",
        universe_key, len(results), last_updated_at,
    )
    return SwingResponse(
        results=results,
        regime=regime_out,
        last_updated_at=last_updated_at,
    )


@router.post("/swing", response_model=SwingResponse)
async def run_swing(req: SwingRequest) -> SwingResponse:
    """Run swing pipeline on a custom symbol list. Strategy gates are bypassed
    so every requested symbol with sufficient price history is returned."""
    if not req.symbols:
        raise HTTPException(status_code=422, detail="symbols list is empty")

    raw, regime_state = await asyncio.to_thread(run_scan, req.symbols, 4, req.bypass_gates)
    _inject_composite(raw)
    results = [SwingResultOut(**r) for r in raw]
    return SwingResponse(
        results=results,
        regime=_regime_to_out(regime_state) if regime_state is not None else None,
    )
