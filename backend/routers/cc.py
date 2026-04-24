"""
POST /api/screener/cc       — custom symbol list
GET  /api/screener/cc/scan  — universe scan, top-N by CC score
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from services.data_service import get_risk_free_rate
from services.cc_service import CcError, CcResult, CcStrikeResult, process_cc_symbol
from services.universe import MOMENTUM_UNIVERSE, UNIVERSE_SIZE

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


class CcResultOut(BaseModel):
    symbol: str
    price: float
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


class CcErrorOut(BaseModel):
    symbol: str
    reason: str


class CcResponse(BaseModel):
    results: List[CcResultOut]
    errors: List[CcErrorOut]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/cc", response_model=CcResponse)
async def run_cc_screener(request: CcRequest) -> CcResponse:
    """Runs the CC screener for the provided symbols."""
    if request.minDTE > request.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting CC screener for %d symbols, DTE %d\u2013%d, rf=%.3f",
        len(request.symbols), request.minDTE, request.maxDTE, rf_rate,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_cc_symbol, symbol, request.minDTE, request.maxDTE, rf_rate
            )

    pairs = await asyncio.gather(*[process_one(s) for s in request.symbols])

    results: list[CcResultOut] = []
    errors: list[CcErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(CcErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("CC screener complete: %d results, %d errors", len(results), len(errors))
    return CcResponse(results=results, errors=errors)


@router.get("/cc/scan", response_model=CcResponse)
async def run_cc_scan(
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=30, ge=1, le=90),
    max_dte: int = Query(default=60, ge=1, le=90),
) -> CcResponse:
    """Scans the full curated universe and returns the top_n CC results by score."""
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting CC universe scan (%d stocks), DTE %d\u2013%d, top_n=%d",
        UNIVERSE_SIZE, min_dte, max_dte, top_n,
    )

    sem = asyncio.Semaphore(10)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(process_cc_symbol, symbol, min_dte, max_dte, rf_rate)

    pairs = await asyncio.gather(*[process_one(s) for s in MOMENTUM_UNIVERSE])

    results: list[CcResultOut] = []
    errors: list[CcErrorOut] = []
    for result_list, error in pairs:
        for result in result_list:
            results.append(_to_out(result))
        if error is not None:
            errors.append(CcErrorOut(symbol=error.symbol, reason=error.reason))

    results.sort(key=lambda r: r.best_cc_score, reverse=True)
    top_results = results[:top_n]

    logger.info(
        "CC scan complete: returning top %d of %d (errors=%d)",
        len(top_results), UNIVERSE_SIZE, len(errors),
    )
    return CcResponse(results=top_results, errors=errors)


def _to_out(r: CcResult) -> CcResultOut:
    return CcResultOut(
        symbol=r.symbol,
        price=r.price,
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
            )
            for s in r.strikes
        ],
        best_cc_score=r.best_cc_score,
        using_hv_fallback=r.using_hv_fallback,
        expected_move=r.expected_move,
        dist_from_52w_high_pct=r.dist_from_52w_high_pct,
    )
