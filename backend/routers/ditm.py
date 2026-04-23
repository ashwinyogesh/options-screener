"""
POST /api/screener/ditm       — custom symbol list
GET  /api/screener/ditm/scan  — universe scan, top-N by DITM score
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from services.data_service import get_risk_free_rate
from services.ditm_service import DitmError, DitmResult, DitmStrikeResult, process_ditm_symbol
from services.universe import MOMENTUM_UNIVERSE, UNIVERSE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["ditm"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class DitmRequest(BaseModel):
    symbols: List[str]
    minDTE: int = 90
    maxDTE: int = 210

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
        if v < 1 or v > 730:
            raise ValueError("DTE must be between 1 and 730")
        return v


class DitmStrikeResultOut(BaseModel):
    strike: float
    delta: float
    premium: float
    intrinsic: float
    extrinsic: float
    extrinsic_pct: float
    moneyness_pct: float
    leverage: float
    bid_ask_spread_pct: Optional[float]
    env_score: float
    strike_score: float
    ditm_score: float
    is_best: bool
    iv_fallback: bool
    stale_premium: bool


class DitmResultOut(BaseModel):
    symbol: str
    price: float
    sma_ratio: Optional[float]
    rsi: Optional[float]
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_support_1: Optional[float]
    vol_support_2: Optional[float]
    vol_support_3: Optional[float]
    dte: int
    expiration: str
    strikes: List[DitmStrikeResultOut]
    best_ditm_score: float
    using_hv_fallback: bool


class DitmErrorOut(BaseModel):
    symbol: str
    reason: str


class DitmResponse(BaseModel):
    results: List[DitmResultOut]
    errors: List[DitmErrorOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_result_out(r: DitmResult) -> DitmResultOut:
    return DitmResultOut(
        symbol=r.symbol,
        price=r.price,
        sma_ratio=None if (r.sma_ratio != r.sma_ratio) else r.sma_ratio,
        rsi=None if (r.rsi != r.rsi) else r.rsi,
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
            DitmStrikeResultOut(
                strike=s.strike,
                delta=s.delta,
                premium=s.premium,
                intrinsic=s.intrinsic,
                extrinsic=s.extrinsic,
                extrinsic_pct=s.extrinsic_pct,
                moneyness_pct=s.moneyness_pct,
                leverage=s.leverage,
                bid_ask_spread_pct=s.bid_ask_spread_pct,
                env_score=s.env_score,
                strike_score=s.strike_score,
                ditm_score=s.ditm_score,
                is_best=s.is_best,
                iv_fallback=s.iv_fallback,
                stale_premium=s.stale_premium,
            )
            for s in r.strikes
        ],
        best_ditm_score=r.best_ditm_score,
        using_hv_fallback=r.using_hv_fallback,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/ditm", response_model=DitmResponse)
async def screen_ditm(request: DitmRequest) -> DitmResponse:
    rf_rate = get_risk_free_rate()
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(sym: str) -> tuple[list[DitmResult], Optional[DitmError]]:
        async with sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: process_ditm_symbol(sym, request.minDTE, request.maxDTE, rf_rate),
            )

    tasks = [process_one(sym) for sym in request.symbols]
    pairs = await asyncio.gather(*tasks)

    all_results: list[DitmResultOut] = []
    all_errors: list[DitmErrorOut] = []
    for results, err in pairs:
        for r in results:
            all_results.append(_to_result_out(r))
        if err:
            all_errors.append(DitmErrorOut(symbol=err.symbol, reason=err.reason))

    return DitmResponse(results=all_results, errors=all_errors)


@router.get("/ditm/scan", response_model=DitmResponse)
async def scan_ditm(
    top_n: int = Query(default=15, ge=1, le=50, alias="topN"),
    min_dte: int = Query(default=90, ge=1, le=730, alias="minDTE"),
    max_dte: int = Query(default=210, ge=1, le=730, alias="maxDTE"),
) -> DitmResponse:
    rf_rate = get_risk_free_rate()
    sem = asyncio.Semaphore(10)

    async def process_one(sym: str) -> tuple[list[DitmResult], Optional[DitmError]]:
        async with sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: process_ditm_symbol(sym, min_dte, max_dte, rf_rate),
            )

    tasks = [process_one(sym) for sym in MOMENTUM_UNIVERSE]
    pairs = await asyncio.gather(*tasks)

    all_results: list[DitmResult] = []
    all_errors: list[DitmErrorOut] = []
    for results, err in pairs:
        all_results.extend(results)
        if err:
            all_errors.append(DitmErrorOut(symbol=err.symbol, reason=err.reason))

    # Sort all results by best_ditm_score, take top N unique symbols
    all_results.sort(key=lambda r: r.best_ditm_score, reverse=True)
    seen: set[str] = set()
    top_results: list[DitmResultOut] = []
    for r in all_results:
        if r.symbol not in seen:
            seen.add(r.symbol)
            top_results.append(_to_result_out(r))
            if len(top_results) >= top_n:
                break

    return DitmResponse(results=top_results, errors=all_errors)
