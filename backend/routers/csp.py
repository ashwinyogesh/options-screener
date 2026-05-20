"""
POST /api/screener/csp
Validates the request, runs the screener for each symbol sequentially,
and returns results + per-symbol errors.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from limiter import limiter

from services.csp_service import CspResult, process_symbol
from services.data_service import get_risk_free_rate
from services.swing.regime import get_vix_context
from services.em_scan_service import EmRankError, EmRankResult, process_em_symbol
from services.scan_cache import ScanCache
from services.screener.result_store import ScreenerStoreEmpty, get_csp_results
from services.screener_insight_service import InsightError, InsightRequest, InsightResult, get_insight
from services.universe import UNIVERSES, get_universe

_em_scan_cache: ScanCache = ScanCache()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["screener"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5  # max parallel yfinance fetches


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CspRequest(BaseModel):
    symbols: List[str]
    minDTE: int = 30
    maxDTE: int = 60
    maxCapital: Optional[float] = None  # strike × 100 collateral cap; None = no constraint

    @field_validator("maxCapital")
    @classmethod
    def validate_max_capital(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 100:
            raise ValueError("maxCapital must be at least 100 (minimum $100 collateral)")
        return v

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
        # Prevent excessively long symbols (basic injection guard)
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


class CspStrikeResultOut(BaseModel):
    strike: float
    delta: float
    premium: float
    annualized_return: float
    bid_ask_spread_pct: Optional[float]
    env_score: float
    strike_score: float
    csp_score: float
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


class CspResultOut(BaseModel):
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
    vol_support_126_1: Optional[float]
    vol_support_126_2: Optional[float]
    vol_support_126_3: Optional[float]
    dte: int
    expiration: str
    strikes: List[CspStrikeResultOut]
    best_csp_score: float
    using_hv_fallback: bool
    expected_move: float
    dist_from_52w_high_pct: float
    chain_median_oi: float


class CspErrorOut(BaseModel):
    symbol: str
    reason: str


class CspResponse(BaseModel):
    results: List[CspResultOut]
    errors: List[CspErrorOut]
    last_updated_at: Optional[str] = None
    vix_level: Optional[float] = None
    vix_percentile: Optional[float] = None
    vol_regime: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/csp", response_model=CspResponse)
@limiter.limit("10/minute")
async def run_csp_screener(request: Request, body: CspRequest) -> CspResponse:
    """
    Runs the CSP screener for the provided symbols.
    Symbols that fail are returned in the errors list; others still appear in results.
    """
    if body.minDTE > body.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting CSP screener for %d symbols, DTE %d\u2013%d, rf=%.3f",
        len(body.symbols),
        body.minDTE,
        body.maxDTE,
        rf_rate,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_symbol, symbol,
                min_dte=body.minDTE, max_dte=body.maxDTE,
                rf_rate=rf_rate, max_capital=body.maxCapital,
            )

    pairs = await asyncio.gather(*[process_one(s) for s in body.symbols])
    _ = request  # consumed by @limiter.limit

    results: list[CspResultOut] = []
    errors: list[CspErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(CspErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("Screener complete: %d results, %d errors", len(results), len(errors))
    vix_ctx = await asyncio.to_thread(get_vix_context)
    return CspResponse(results=results, errors=errors, **vix_ctx)


@router.get("/csp/scan", response_model=CspResponse)
@limiter.limit("30/minute")
async def run_csp_scan(
    request: Request,
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=30, ge=1, le=90),
    max_dte: int = Query(default=60, ge=1, le=90),
    universe: str = Query(default="all", description=f"Universe key: one of {sorted(UNIVERSES)}"),
    max_capital: Optional[float] = Query(default=None, ge=100, description="Max capital per contract ($); only strikes where strike\u00d7100 \u2264 max_capital are returned"),
) -> CspResponse:
    """
    Returns precomputed CSP universe scan results. Results are updated every
    15 min during market hours and every 4 h outside market hours by a
    background Container Apps Job (ADR-0024). Custom symbol lists use POST /csp.
    """
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    universe_key, symbols = get_universe(universe)

    try:
        rows, last_updated_at, _oldest_age = await asyncio.to_thread(
            get_csp_results, symbols, min_dte, max_dte, top_n, max_capital
        )
    except ScreenerStoreEmpty as exc:
        logger.warning("CSP precomputed store empty: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Precomputed CSP results are not yet available. "
                "The background worker is populating the store — retry in a few minutes."
            ),
        ) from exc

    logger.info(
        "CSP scan (precomputed): universe=%s returning %d rows, last_updated=%s",
        universe_key, len(rows), last_updated_at,
    )
    vix_ctx = await asyncio.to_thread(get_vix_context)
    return CspResponse(
        results=[_to_out(r) for r in rows],
        errors=[],
        last_updated_at=last_updated_at,
        **vix_ctx,
    )


def _to_out(r: CspResult) -> CspResultOut:
    return CspResultOut(
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
        vol_support_126_1=r.vol_support_126_1,
        vol_support_126_2=r.vol_support_126_2,
        vol_support_126_3=r.vol_support_126_3,
        dte=r.dte,
        expiration=r.expiration,
        strikes=[
            CspStrikeResultOut(
                strike=s.strike,
                delta=s.delta,
                premium=s.premium,
                annualized_return=s.annualized_return,
                bid_ask_spread_pct=s.bid_ask_spread_pct,
                env_score=s.env_score,
                strike_score=s.strike_score,
                csp_score=s.csp_score,
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
        best_csp_score=r.best_csp_score,
        using_hv_fallback=r.using_hv_fallback,
        expected_move=r.expected_move,
        dist_from_52w_high_pct=r.dist_from_52w_high_pct,
        chain_median_oi=r.chain_median_oi,
    )


# ---------------------------------------------------------------------------
# Insight endpoint
# ---------------------------------------------------------------------------

class InsightRequestIn(BaseModel):
    symbol: str
    price: float
    strike: float
    premium: float
    dte: int
    expiration: str
    earnings_within_dte: bool = False
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str
    strike_detail: str
    roc_annualized: Optional[float] = None
    rsi: float
    iv_hv_ratio: Optional[float] = None       # kept for back-compat
    iv_percentile: Optional[float] = None     # v3.3 scored ENV factor
    dist_from_52w_high_pct: float


class InsightResultOut(BaseModel):
    reasoning: str
    verdict: str
    confidence: float
    summary: str
    regime_drivers: str
    current_regime: str
    stock_cycle: str
    bear_band: str
    normal_band: str
    bull_band: str
    ownership_case: str
    key_risk: str
    vix_regime: str


@router.post("/csp/insight", response_model=InsightResultOut)
@limiter.limit("10/minute")
async def get_csp_insight(http_request: Request, request: InsightRequestIn) -> InsightResultOut:
    """
    Calls Azure OpenAI with the scored CSP row + recent news to produce
    a plain-English ENTER / WAIT / SKIP verdict with rationale.
    """
    svc_req = InsightRequest(
        symbol=request.symbol.strip().upper(),
        price=request.price,
        strike=request.strike,
        premium=request.premium,
        dte=request.dte,
        expiration=request.expiration,
        earnings_within_dte=request.earnings_within_dte,
        env_score=request.env_score,
        strike_score=request.strike_score,
        final_score=request.final_score,
        env_detail=request.env_detail,
        strike_detail=request.strike_detail,
        roc_annualized=request.roc_annualized,
        rsi=request.rsi,
        iv_hv_ratio=request.iv_hv_ratio,
        iv_percentile=request.iv_percentile,
        dist_from_52w_high_pct=request.dist_from_52w_high_pct,
    )
    try:
        result: InsightResult = await asyncio.to_thread(get_insight, svc_req)
    except InsightError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return InsightResultOut(
        reasoning=result.reasoning,
        verdict=result.verdict,
        confidence=result.confidence,
        summary=result.summary,
        regime_drivers=result.regime_drivers,
        current_regime=result.current_regime,
        stock_cycle=result.stock_cycle,
        bear_band=result.bear_band,
        normal_band=result.normal_band,
        bull_band=result.bull_band,
        ownership_case=result.ownership_case,
        key_risk=result.key_risk,
        vix_regime=result.vix_regime,
    )


# ---------------------------------------------------------------------------
# EM Rank endpoints
# ---------------------------------------------------------------------------

class EmRankRequest(BaseModel):
    symbols: List[str]
    minDTE: int = 30
    maxDTE: int = 60
    maxCapital: Optional[float] = None

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


class EmRankStrikeOut(BaseModel):
    strike: float
    bid: float
    ask: float
    mid: float
    spread_pct: Optional[float]
    delta: float
    oi_vol: int
    roc_annualized: Optional[float]
    otm_pct: float
    is_em_strike: bool
    iv_fallback: bool
    stale_premium: bool


class EmRankResultOut(BaseModel):
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
    vol_support_126_1: Optional[float]
    vol_support_126_2: Optional[float]
    vol_support_126_3: Optional[float]
    dte: int
    expiration: str
    expected_move: float
    chain_median_oi: float
    dist_from_52w_high_pct: float
    iv_hv_ratio: Optional[float]
    strikes: List[EmRankStrikeOut]
    best_roc: float
    using_hv_fallback: bool


class EmRankErrorOut(BaseModel):
    symbol: str
    reason: str


class EmRankResponse(BaseModel):
    results: List[EmRankResultOut]
    errors: List[EmRankErrorOut]


@router.post("/csp/em-rank", response_model=EmRankResponse)
@limiter.limit("10/minute")
async def run_em_rank(http_request: Request, request: EmRankRequest) -> EmRankResponse:
    """
    EM Rank screener (manual symbols).
    Returns strikes just below the 1σ Expected Move, ranked by ROC.
    """
    if request.minDTE > request.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_em_symbol, symbol,
                min_dte=request.minDTE, max_dte=request.maxDTE,
                rf_rate=rf_rate, max_capital=request.maxCapital,
            )

    pairs = await asyncio.gather(*[process_one(s) for s in request.symbols])

    results: list[EmRankResultOut] = []
    errors: list[EmRankErrorOut] = []
    for result_list, error in pairs:
        for r in result_list:
            results.append(_to_em_out(r))
        if error is not None:
            errors.append(EmRankErrorOut(symbol=error.symbol, reason=error.reason))

    # Sort by best_roc descending across all expirations per symbol
    results = _sort_em_by_roc(results)
    return EmRankResponse(results=results, errors=errors)


@router.get("/csp/em-scan", response_model=EmRankResponse)
@limiter.limit("4/minute")
async def run_em_scan(
    request: Request,
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=30, ge=1, le=90),
    max_dte: int = Query(default=60, ge=1, le=90),
    universe: str = Query(default="all", description=f"Universe key: one of {sorted(UNIVERSES)}"),
    max_capital: Optional[float] = Query(default=None, ge=100),
) -> EmRankResponse:
    """
    EM Rank screener (universe scan).
    Returns the top_n symbols ranked by highest annualized ROC at the EM strike.
    """
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    universe_key, symbols = get_universe(universe)
    cache_key = f"em:{universe_key}:{top_n}:{min_dte}:{max_dte}:{max_capital}"
    cached = _em_scan_cache.get(cache_key)
    if cached is not None:
        return cached

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting EM scan universe=%s (%d stocks), DTE %d\u2013%d, top_n=%d",
        universe_key, len(symbols), min_dte, max_dte, top_n,
    )

    sem = asyncio.Semaphore(10)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_em_symbol, symbol,
                min_dte=min_dte, max_dte=max_dte,
                rf_rate=rf_rate, max_capital=max_capital,
            )

    pairs = await asyncio.gather(*[process_one(s) for s in symbols])

    all_results: list[EmRankResultOut] = []
    errors: list[EmRankErrorOut] = []
    for result_list, error in pairs:
        for r in result_list:
            all_results.append(_to_em_out(r))
        if error is not None:
            errors.append(EmRankErrorOut(symbol=error.symbol, reason=error.reason))

    # Rank symbols by their best ROC, then take top_n symbols' rows
    sorted_results = _sort_em_by_roc(all_results)
    top_symbols = _top_n_symbols(sorted_results, top_n)
    top_results = [r for r in sorted_results if r.symbol in top_symbols]

    logger.info(
        "EM scan complete: universe=%s returning top %d of %d (errors=%d)",
        universe_key, len(top_symbols), len(symbols), len(errors),
    )
    response = EmRankResponse(results=top_results, errors=errors)
    _em_scan_cache.set(cache_key, response)
    return response


def _to_em_out(r: EmRankResult) -> EmRankResultOut:
    return EmRankResultOut(
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
        vol_support_126_1=r.vol_support_126_1,
        vol_support_126_2=r.vol_support_126_2,
        vol_support_126_3=r.vol_support_126_3,
        dte=r.dte,
        expiration=r.expiration,
        expected_move=r.expected_move,
        chain_median_oi=r.chain_median_oi,
        dist_from_52w_high_pct=r.dist_from_52w_high_pct,
        iv_hv_ratio=r.iv_hv_ratio,
        strikes=[
            EmRankStrikeOut(
                strike=s.strike,
                bid=s.bid,
                ask=s.ask,
                mid=s.mid,
                spread_pct=s.spread_pct,
                delta=s.delta,
                oi_vol=s.oi_vol,
                roc_annualized=s.roc_annualized,
                otm_pct=s.otm_pct,
                is_em_strike=s.is_em_strike,
                iv_fallback=s.iv_fallback,
                stale_premium=s.stale_premium,
            )
            for s in r.strikes
        ],
        best_roc=r.best_roc,
        using_hv_fallback=r.using_hv_fallback,
    )


def _sort_em_by_roc(results: list[EmRankResultOut]) -> list[EmRankResultOut]:
    """Sort flat per-expiration rows so that rows belonging to higher-ROC symbols
    appear first. Within a symbol, rows are ordered by DTE ascending."""
    # Find each symbol's max best_roc across all its expiration rows
    sym_best: dict[str, float] = {}
    for r in results:
        if r.symbol not in sym_best or r.best_roc > sym_best[r.symbol]:
            sym_best[r.symbol] = r.best_roc
    return sorted(results, key=lambda r: (sym_best.get(r.symbol, 0.0), r.best_roc), reverse=True)


def _top_n_symbols(sorted_results: list[EmRankResultOut], n: int) -> set[str]:
    """Return the first n distinct symbols from a pre-sorted list."""
    seen: set[str] = set()
    for r in sorted_results:
        if len(seen) >= n:
            break
        seen.add(r.symbol)
    return seen
