"""
POST /api/screener/ditm
GET  /api/screener/ditm/scan

DITM Long Call screener — mirrors csp.py router structure exactly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from services.data_service import get_risk_free_rate
from services.scan_cache import ditm_scan_cache
from services.ditm_service import (
    DitmResult,
    get_macro_context,
    process_symbol,
)
from services.universe import UNIVERSES, get_universe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["screener"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class DitmRequest(BaseModel):
    symbols: List[str]
    minDTE: int = 90
    maxDTE: int = 180

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
        if not (30 <= v <= 730):
            raise ValueError("DTE values must be between 30 and 730")
        return v

    @field_validator("maxDTE")
    @classmethod
    def validate_dte_range(cls, v: int, info) -> int:
        min_dte = info.data.get("minDTE")
        if min_dte is not None and v < min_dte:
            raise ValueError("maxDTE must be >= minDTE")
        return v


class DitmStrikeResultOut(BaseModel):
    strike: float
    delta: float
    mid: float
    extrinsic_pct: float
    theta_annualized_pct: float
    breakeven_pct: float
    capital_efficiency_pct: float
    bid_ask_spread_pct: Optional[float]
    chain_oi: int
    env_score: float
    strike_score: float
    ditm_score: float
    env_detail: str
    strike_detail: str
    is_best: bool
    iv_fallback: bool


class DitmResultOut(BaseModel):
    symbol: str
    price: float
    sma_ratio: float
    hv_rank: float
    hv30: float
    weekly_rsi: float
    ret_200d: float
    dist_from_52w_high_pct: float
    earnings_date: Optional[str]
    days_to_earnings: Optional[int]
    earnings_within_dte: bool
    dte: int
    expiration: str
    strikes: List[DitmStrikeResultOut]
    best_ditm_score: float
    gap_3d_pct: float
    macro_hold: bool
    chain_median_oi: float


class DitmErrorOut(BaseModel):
    symbol: str
    reason: str


class DitmResponse(BaseModel):
    results: List[DitmResultOut]
    errors: List[DitmErrorOut]
    macro_pass: bool
    vix_level: Optional[float] = None
    vix_5d_change: Optional[float] = None
    spy_above_sma200: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/ditm", response_model=DitmResponse)
async def run_ditm_screener(request: DitmRequest) -> DitmResponse:
    """
    Runs the DITM long call screener for the provided symbols.
    Symbols that fail are returned in the errors list; others still appear in results.
    """
    if request.minDTE > request.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    macro_ctx = await asyncio.to_thread(get_macro_context)
    logger.info(
        "Starting DITM screener for %d symbols, DTE %d–%d, rf=%.3f, macro_pass=%s",
        len(request.symbols),
        request.minDTE,
        request.maxDTE,
        rf_rate,
        macro_ctx["macro_pass"],
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_symbol, symbol, request.minDTE, request.maxDTE, rf_rate, macro_ctx
            )

    pairs = await asyncio.gather(*[process_one(s) for s in request.symbols])

    results: list[DitmResultOut] = []
    errors: list[DitmErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(DitmErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("DITM screener complete: %d results, %d errors", len(results), len(errors))
    return DitmResponse(
        results=results,
        errors=errors,
        macro_pass=macro_ctx["macro_pass"],
        vix_level=macro_ctx.get("vix_level"),
        vix_5d_change=macro_ctx.get("vix_5d_change"),
        spy_above_sma200=macro_ctx.get("spy_above_sma200", True),
    )


@router.get("/ditm/scan", response_model=DitmResponse)
async def run_ditm_scan(
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=180, ge=30, le=730),
    max_dte: int = Query(default=365, ge=30, le=730),
    universe: str = Query(default="all", description=f"Universe key: one of {sorted(UNIVERSES)}"),
) -> DitmResponse:
    """
    Scans the selected universe and returns top_n DITM results ranked by score.
    """
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    universe_key, symbols = get_universe(universe)
    cache_key = f"{universe_key}:{top_n}:{min_dte}:{max_dte}"
    cached = ditm_scan_cache.get(cache_key)
    if cached is not None:
        logger.info("DITM scan cache hit: %s", cache_key)
        return cached
    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    macro_ctx = await asyncio.to_thread(get_macro_context)
    logger.info(
        "Starting DITM scan universe=%s (%d stocks), DTE %d–%d, top_n=%d",
        universe_key, len(symbols), min_dte, max_dte, top_n,
    )

    sem = asyncio.Semaphore(10)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_symbol, symbol, min_dte, max_dte, rf_rate, macro_ctx
            )

    pairs = await asyncio.gather(*[process_one(s) for s in symbols])

    results: list[DitmResultOut] = []
    errors: list[DitmErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(DitmErrorOut(symbol=error.symbol, reason=error.reason))

    results.sort(key=lambda r: r.best_ditm_score, reverse=True)
    top_results = results[:top_n]

    logger.info(
        "DITM scan complete: universe=%s returning top %d of %d (errors=%d)",
        universe_key, len(top_results), len(symbols), len(errors),
    )
    response = DitmResponse(
        results=top_results,
        errors=errors,
        macro_pass=macro_ctx["macro_pass"],
        vix_level=macro_ctx.get("vix_level"),
        vix_5d_change=macro_ctx.get("vix_5d_change"),
        spy_above_sma200=macro_ctx.get("spy_above_sma200", True),
    )
    ditm_scan_cache.set(cache_key, response)
    return response


def _to_out(r: DitmResult) -> DitmResultOut:
    return DitmResultOut(
        symbol=r.symbol,
        price=r.price,
        sma_ratio=r.sma_ratio,
        hv_rank=r.hv_rank,
        hv30=r.hv30,
        weekly_rsi=r.weekly_rsi,
        ret_200d=r.ret_200d,
        dist_from_52w_high_pct=r.dist_from_52w_high_pct,
        earnings_date=r.earnings_date,
        days_to_earnings=r.days_to_earnings,
        earnings_within_dte=r.earnings_within_dte,
        dte=r.dte,
        expiration=r.expiration,
        strikes=[
            DitmStrikeResultOut(
                strike=s.strike,
                delta=s.delta,
                mid=s.mid,
                extrinsic_pct=s.extrinsic_pct,
                theta_annualized_pct=s.theta_annualized_pct,
                breakeven_pct=s.breakeven_pct,
                capital_efficiency_pct=s.capital_efficiency_pct,
                bid_ask_spread_pct=s.bid_ask_spread_pct,
                chain_oi=s.chain_oi,
                env_score=s.env_score,
                strike_score=s.strike_score,
                ditm_score=s.ditm_score,
                env_detail=s.env_detail,
                strike_detail=s.strike_detail,
                is_best=s.is_best,
                iv_fallback=s.iv_fallback,
            )
            for s in r.strikes
        ],
        best_ditm_score=r.best_ditm_score,
        gap_3d_pct=r.gap_3d_pct,
        macro_hold=r.macro_hold,
        chain_median_oi=r.chain_median_oi,
    )
