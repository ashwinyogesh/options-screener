"""
POST /api/screener/csp
Validates the request, runs the screener for each symbol sequentially,
and returns results + per-symbol errors.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from services.data_service import get_risk_free_rate
from services.screener_service import ScreenerError, ScreenerResult, StrikeResult, process_symbol
from services.universe import MOMENTUM_UNIVERSE, UNIVERSE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["screener"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5  # max parallel yfinance fetches


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ScreenerRequest(BaseModel):
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


class StrikeResultOut(BaseModel):
    strike: float
    delta: float
    premium: float
    annualized_return: float
    bid_ask_spread_pct: Optional[float]
    csp_score: float
    is_best: bool


class ScreenerResultOut(BaseModel):
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
    vol_support_1: Optional[float]
    vol_support_2: Optional[float]
    vol_support_3: Optional[float]
    dte: int
    expiration: str
    strikes: List[StrikeResultOut]
    best_csp_score: float


class ScreenerErrorOut(BaseModel):
    symbol: str
    reason: str


class ScreenerResponse(BaseModel):
    results: List[ScreenerResultOut]
    errors: List[ScreenerErrorOut]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/csp", response_model=ScreenerResponse)
async def run_csp_screener(request: ScreenerRequest) -> ScreenerResponse:
    """
    Runs the CSP screener for the provided symbols.
    Symbols that fail are returned in the errors list; others still appear in results.
    """
    if request.minDTE > request.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting CSP screener for %d symbols, DTE %d\u2013%d, rf=%.3f",
        len(request.symbols),
        request.minDTE,
        request.maxDTE,
        rf_rate,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_symbol, symbol, request.minDTE, request.maxDTE, rf_rate
            )

    pairs = await asyncio.gather(*[process_one(s) for s in request.symbols])

    results: list[ScreenerResultOut] = []
    errors: list[ScreenerErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(ScreenerErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("Screener complete: %d results, %d errors", len(results), len(errors))
    return ScreenerResponse(results=results, errors=errors)


@router.get("/csp/scan", response_model=ScreenerResponse)
async def run_csp_scan(
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=30, ge=1, le=90),
    max_dte: int = Query(default=60, ge=1, le=90),
) -> ScreenerResponse:
    """
    Scans the full curated universe (~75 stocks) and returns the
    top_n results ranked by CSP composite score descending.
    """
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting CSP universe scan (%d stocks), DTE %d\u2013%d, top_n=%d",
        UNIVERSE_SIZE, min_dte, max_dte, top_n,
    )

    sem = asyncio.Semaphore(10)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(process_symbol, symbol, min_dte, max_dte, rf_rate)

    pairs = await asyncio.gather(*[process_one(s) for s in MOMENTUM_UNIVERSE])

    results: list[ScreenerResultOut] = []
    errors: list[ScreenerErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(ScreenerErrorOut(symbol=error.symbol, reason=error.reason))

    results.sort(key=lambda r: r.best_csp_score, reverse=True)
    top_results = results[:top_n]

    logger.info(
        "CSP scan complete: returning top %d of %d (errors=%d)",
        len(top_results), UNIVERSE_SIZE, len(errors),
    )
    return ScreenerResponse(results=top_results, errors=errors)


def _to_out(r: ScreenerResult) -> ScreenerResultOut:
    return ScreenerResultOut(
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
        vol_support_1=r.vol_support_1,
        vol_support_2=r.vol_support_2,
        vol_support_3=r.vol_support_3,
        dte=r.dte,
        expiration=r.expiration,
        strikes=[
            StrikeResultOut(
                strike=s.strike,
                delta=s.delta,
                premium=s.premium,
                annualized_return=s.annualized_return,
                bid_ask_spread_pct=s.bid_ask_spread_pct,
                csp_score=s.csp_score,
                is_best=s.is_best,
            )
            for s in r.strikes
        ],
        best_csp_score=r.best_csp_score,
    )
