"""
POST /api/screener/ditm
GET  /api/screener/ditm/scan

DITM Long Call screener — mirrors csp.py router structure exactly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from limiter import limiter

from services.data_service import get_risk_free_rate
from services.ditm_service import (
    DitmResult,
    get_macro_context,
    process_symbol,
)
from services.screener.result_store import ScreenerStoreEmpty, get_ditm_results
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
    last_updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/ditm", response_model=DitmResponse)
@limiter.limit("10/minute")
async def run_ditm_screener(request: Request, body: DitmRequest) -> DitmResponse:
    """
    Runs the DITM long call screener for the provided symbols.
    Symbols that fail are returned in the errors list; others still appear in results.
    """
    _ = request  # consumed by @limiter.limit
    if body.minDTE > body.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    macro_ctx = await asyncio.to_thread(get_macro_context)
    logger.info(
        "Starting DITM screener for %d symbols, DTE %d–%d, rf=%.3f, macro_pass=%s",
        len(body.symbols),
        body.minDTE,
        body.maxDTE,
        rf_rate,
        macro_ctx["macro_pass"],
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_symbol, symbol, body.minDTE, body.maxDTE, rf_rate, macro_ctx
            )

    pairs = await asyncio.gather(*[process_one(s) for s in body.symbols])

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
@limiter.limit("30/minute")
async def run_ditm_scan(
    request: Request,
    top_n: int = Query(default=20, ge=1, le=50),
    min_dte: int = Query(default=180, ge=30, le=730),
    max_dte: int = Query(default=365, ge=30, le=730),
    universe: str = Query(default="all", description=f"Universe key: one of {sorted(UNIVERSES)}"),
) -> DitmResponse:
    """Returns precomputed DITM universe scan results (ADR-0024). Custom lists use POST /ditm."""
    if min_dte > max_dte:
        raise HTTPException(status_code=422, detail="min_dte must be <= max_dte")

    universe_key, symbols = get_universe(universe)

    try:
        rows, macro_fields, last_updated_at, _oldest_age = await asyncio.to_thread(
            get_ditm_results, symbols, min_dte, max_dte, top_n
        )
    except ScreenerStoreEmpty as exc:
        logger.warning("DITM precomputed store empty: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Precomputed DITM results are not yet available. "
                "The background worker is populating the store — retry in a few minutes."
            ),
        ) from exc

    logger.info(
        "DITM scan (precomputed): universe=%s returning %d rows, last_updated=%s",
        universe_key, len(rows), last_updated_at,
    )
    # Always fetch fresh macro context — VIX may not be present in precomputed docs.
    macro_ctx = await asyncio.to_thread(get_macro_context)
    return DitmResponse(
        results=[_to_out(r) for r in rows],
        errors=[],
        macro_pass=macro_ctx["macro_pass"],
        vix_level=macro_ctx.get("vix_level"),
        vix_5d_change=macro_ctx.get("vix_5d_change"),
        spy_above_sma200=macro_ctx.get("spy_above_sma200", True),
        last_updated_at=last_updated_at,
    )


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
