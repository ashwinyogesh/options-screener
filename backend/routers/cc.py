"""
POST /api/screener/cc       — custom symbol list
GET  /api/screener/cc/scan  — universe scan, top-N by CC score
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from limiter import limiter

from services.cc_service import CcResult, process_cc_symbol
from services.cc_backtest_service import (
    BacktestError,
    BacktestResult,
    backtest_ticker,
)
from services.data_service import get_risk_free_rate
from services.scan_cache import cc_backtest_cache
from services.swing.regime import get_vix_context
from services.screener.result_store import ScreenerStoreEmpty, get_cc_results
from services.universe import UNIVERSES, get_universe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["cc"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CcRequest(BaseModel):
    symbols: List[str]
    minDTE: int = 30
    maxDTE: int = 60

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
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

    @field_validator("minDTE", "maxDTE")
    @classmethod
    def validate_dte(cls, v: int) -> int:
        if not (1 <= v <= 90):
            raise ValueError("DTE values must be between 1 and 90")
        return v

    @field_validator("maxDTE")
    @classmethod
    def validate_dte_range(cls, v: int, info) -> int:
        min_dte = info.data.get("minDTE")
        if min_dte is not None and v < min_dte:
            raise ValueError("maxDTE must be >= minDTE")
        return v


class CcStrikeResultOut(BaseModel):
    strike: float
    delta: float
    premium: float
    annualized_return: float
    bid_ask_spread_pct: Optional[float]
    env_score: float
    strike_score: float
    cc_score: float
    env_detail: str
    strike_detail: str
    is_best: bool
    iv_fallback: bool
    stale_premium: bool
    iv_hv_ratio: Optional[float]
    dist_pct: Optional[float]
    em_buffer_pct: Optional[float]
    otm_pct: float
    lq_count: int
    roc_annualized: Optional[float] = None
    iv_stale: bool = False


class CcResultOut(BaseModel):
    symbol: str
    price: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    sma_ratio: float
    rsi: float
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_resistance_126_1: Optional[float]
    vol_resistance_126_2: Optional[float]
    vol_resistance_126_3: Optional[float]
    dte: int
    expiration: str
    strikes: List[CcStrikeResultOut]
    best_cc_score: float
    using_hv_fallback: bool
    expected_move: float
    dist_from_52w_high_pct: float
    chain_median_oi: float


class CcErrorOut(BaseModel):
    symbol: str
    reason: str


class CcResponse(BaseModel):
    results: List[CcResultOut]
    errors: List[CcErrorOut]
    last_updated_at: Optional[str] = None
    vix_level: Optional[float] = None
    vix_percentile: Optional[float] = None
    vol_regime: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/cc", response_model=CcResponse)
@limiter.limit("10/minute")
async def run_cc_screener(request: Request, body: CcRequest) -> CcResponse:
    """Runs the CC screener for the provided symbols."""
    if body.minDTE > body.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting CC screener for %d symbols, DTE %d\u2013%d, rf=%.3f",
        len(body.symbols), body.minDTE, body.maxDTE, rf_rate,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_cc_symbol, symbol, body.minDTE, body.maxDTE, rf_rate
            )

    pairs = await asyncio.gather(*[process_one(s) for s in body.symbols])
    _ = request  # consumed by @limiter.limit

    results: list[CcResultOut] = []
    errors: list[CcErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(CcErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("CC screener complete: %d results, %d errors", len(results), len(errors))
    vix_ctx = await asyncio.to_thread(get_vix_context)
    return CcResponse(results=results, errors=errors, **vix_ctx)


@router.get("/cc/scan", response_model=CcResponse)
@limiter.limit("30/minute")
async def run_cc_scan(
    request: Request,
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=30, ge=1, le=90),
    max_dte: int = Query(default=60, ge=1, le=90),
    universe: str = Query(default="all", description=f"Universe key: one of {sorted(UNIVERSES)}"),
) -> CcResponse:
    """Returns precomputed CC universe scan results (ADR-0024). Custom lists use POST /cc."""
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    universe_key, symbols = get_universe(universe)

    try:
        rows, last_updated_at, _oldest_age = await asyncio.to_thread(
            get_cc_results, symbols, min_dte, max_dte, top_n
        )
    except ScreenerStoreEmpty as exc:
        logger.warning("CC precomputed store empty: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Precomputed CC results are not yet available. "
                "The background worker is populating the store — retry in a few minutes."
            ),
        ) from exc

    logger.info(
        "CC scan (precomputed): universe=%s returning %d rows, last_updated=%s",
        universe_key, len(rows), last_updated_at,
    )
    vix_ctx = await asyncio.to_thread(get_vix_context)
    return CcResponse(
        results=[_to_out(r) for r in rows],
        errors=[],
        last_updated_at=last_updated_at,
        **vix_ctx,
    )


def _to_out(r: CcResult) -> CcResultOut:
    return CcResultOut(
        symbol=r.symbol,
        price=r.price,
        bb_upper=r.bb_upper,
        bb_middle=r.bb_middle,
        bb_lower=r.bb_lower,
        sma_ratio=r.sma_ratio,
        rsi=r.rsi,
        iv_rank=r.iv_rank,
        iv_percentile=r.iv_percentile,
        earnings_date=r.earnings_date,
        earnings_within_dte=r.earnings_within_dte,
        vol_resistance_126_1=r.vol_resistance_126_1,
        vol_resistance_126_2=r.vol_resistance_126_2,
        vol_resistance_126_3=r.vol_resistance_126_3,
        dte=r.dte,
        expiration=r.expiration,
        strikes=[
            CcStrikeResultOut(
                strike=s.strike,
                delta=s.delta,
                premium=s.premium,
                annualized_return=s.annualized_return,
                bid_ask_spread_pct=s.bid_ask_spread_pct,
                env_score=s.env_score,
                strike_score=s.strike_score,
                cc_score=s.cc_score,
                env_detail=s.env_detail,
                strike_detail=s.strike_detail,
                is_best=s.is_best,
                iv_fallback=s.iv_fallback,
                stale_premium=s.stale_premium,
                iv_hv_ratio=s.iv_hv_ratio,
                dist_pct=s.dist_pct,
                em_buffer_pct=s.em_buffer_pct,
                otm_pct=s.otm_pct,
                lq_count=s.lq_count,
                roc_annualized=s.roc_annualized,
                iv_stale=s.iv_stale,
            )
            for s in r.strikes
        ],
        best_cc_score=r.best_cc_score,
        using_hv_fallback=r.using_hv_fallback,
        expected_move=r.expected_move,
        dist_from_52w_high_pct=r.dist_from_52w_high_pct,
        chain_median_oi=r.chain_median_oi,
    )


# ---------------------------------------------------------------------------
# Per-ticker CC backtest (mirrors CSP)
# ---------------------------------------------------------------------------

class BacktestTradeOut(BaseModel):
    scan_date: str
    spot: float
    strike: float
    dte: int
    expiry_date: str
    delta: float
    premium: float
    final_score: float
    env_score: float
    strike_quant_score: float
    spot_at_exp: float
    assigned: int
    pnl_per_contract: float
    realised_roc_annualised: float


class BacktestBucketOut(BaseModel):
    bucket: str
    n: int
    mean_roc: float
    median_roc: float
    win_rate: float
    assign_rate: float


class BacktestSummaryOut(BaseModel):
    n_trades: int
    n_winners: int
    n_losers: int
    n_assigned: int
    win_rate: float
    assign_rate: float
    mean_roc: float
    median_roc: float
    mean_score: float
    spearman_rho: float
    spearman_p: float
    monotone_buckets: bool
    cutoff_delta_roc: float
    equity_curve: List[float]


class BacktestResponse(BaseModel):
    symbol: str
    years: int
    dte: int
    scan_start: str
    scan_end: str
    summary: BacktestSummaryOut
    buckets: List[BacktestBucketOut]
    trades: List[BacktestTradeOut]
    caveats: List[str]


def _to_backtest_response(r: BacktestResult) -> BacktestResponse:
    return BacktestResponse(
        symbol=r.symbol,
        years=r.years,
        dte=r.dte,
        scan_start=r.scan_start,
        scan_end=r.scan_end,
        summary=BacktestSummaryOut(**r.summary.__dict__),
        buckets=[BacktestBucketOut(**b.__dict__) for b in r.buckets],
        trades=[
            BacktestTradeOut(
                scan_date=t.scan_date,
                spot=t.spot,
                strike=t.strike,
                dte=t.dte,
                expiry_date=t.expiry_date,
                delta=t.delta,
                premium=t.premium,
                final_score=t.final_score,
                env_score=t.env_score,
                strike_quant_score=t.strike_quant_score,
                spot_at_exp=t.spot_at_exp,
                assigned=t.assigned,
                pnl_per_contract=t.pnl_per_contract,
                realised_roc_annualised=t.realised_roc_annualised,
            )
            for t in r.trades
        ],
        caveats=r.caveats,
    )


@router.get("/cc/{symbol}/backtest", response_model=BacktestResponse)
@limiter.limit("6/minute")
async def cc_backtest_endpoint(
    request: Request,
    symbol: str,
    years: int = Query(default=2, ge=1, le=5),
    dte: int = Query(default=35, ge=14, le=60),
) -> BacktestResponse:
    """
    Walk-forward CC backtest for one symbol using the live v3.3 scoring
    function. Returns headline stats, per-bucket performance, the equity
    curve, and a per-trade ledger.

    Methodology: HV(30) as IV proxy; BA/LQ omitted (strike score
    renormalised to Δ + ROC); production hard filters preserved
    (delta ∈ [+0.10, +0.35], strike > spot × 0.98). Total CC P&L
    (stock + short call) is realised at expiration.

    Cached for 4 hours per (symbol, years, dte).
    """
    sym = symbol.strip().upper()
    if not sym or len(sym) > 10 or not sym.isalnum():
        raise HTTPException(status_code=422, detail=f"invalid symbol: '{symbol}'")

    _ = request  # consumed by @limiter.limit
    cache_key = f"{sym}:{years}:{dte}"
    cached = cc_backtest_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        result: BacktestResult = await asyncio.to_thread(
            backtest_ticker, sym, years=years, dte=dte,
        )
    except BacktestError as exc:
        raise HTTPException(status_code=404, detail=exc.reason) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("CC backtest failed for %s", sym)
        raise HTTPException(status_code=500, detail=f"backtest failed: {exc}") from exc

    response = _to_backtest_response(result)
    cc_backtest_cache.set(cache_key, response)
    return response
