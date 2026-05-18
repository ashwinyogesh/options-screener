"""Narrative intelligence routes.

GET /api/narrative/tickers/{ticker}/acs — latest ACS for a ticker
GET /api/narrative/tickers/top          — top-N by current ACS
GET /api/narrative/emerging             — stage 1–3 with rising ACS
GET /api/narrative/narratives/{nid}     — cluster detail
GET /api/narrative/alerts               — pg_cron-populated alerts

All endpoints rate-limited via the existing slowapi limiter at 30/min/IP.

Phase 0: routes are registered and respond 503 (Service Unavailable) with a
clear message pointing at the deployment phase that will activate them. This
lets the frontend integrate against stable shapes today.

Layering rule (copilot-instructions.md): routers validate, delegate, and
convert. No business logic here. Service exceptions map to HTTP via the
mapping below.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from limiter import limiter
from services.narrative import read_service
from services.narrative.errors import (
    NarrativeNotFound,
    NarrativeUnavailable,
    TickerNotTracked,
)
from services.narrative.types import (
    AcsScore,
    NarrativeAlert,
    NarrativeCluster,
    TickerDetail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/narrative", tags=["narrative"])


# ---------- Response models ----------


class AcsComponentsOut(BaseModel):
    a_attention_persistence: float = Field(..., ge=0, le=25)
    b_contributor_quality: float = Field(..., ge=0, le=20)
    c_narrative_strength: float = Field(..., ge=0, le=20)
    d_thesis_quality: float = Field(..., ge=0, le=20)
    e_market_confirmation: float = Field(..., ge=0, le=15)


class AcsScoreOut(BaseModel):
    ticker: str
    scored_at: datetime
    acs: float = Field(..., ge=0, le=100)
    acs_ci_lower: float = Field(..., ge=0, le=100)
    acs_ci_upper: float = Field(..., ge=0, le=100)
    components: AcsComponentsOut
    dominant_signal: str
    decay_acs: float
    flags: list[str]
    lifecycle_stage: int = Field(..., ge=0, le=6)
    stage_confidence: float = Field(..., ge=0, le=1)
    # ADR-0023 — continuity fields surfaced on existing endpoints. All optional
    # so pre-ADR-0023 docs (no scorer pass yet) still validate.
    stage_streak_days: int = Field(0, ge=0)
    first_emerged_at: str | None = None
    acs_slope_14d: float | None = None


class DailyBucketOut(BaseModel):
    day: str
    count: int = Field(..., ge=0)
    unique_authors: int = Field(..., ge=0)


class TickerDetailOut(BaseModel):
    ticker: str
    bucket_date: str
    score: AcsScoreOut
    daily_buckets: list[DailyBucketOut]
    tier1_pct: float = Field(..., ge=0, le=1)
    tier2_pct: float = Field(..., ge=0, le=1)
    tier3_pct: float = Field(..., ge=0, le=1)
    mentions_14d: int = Field(..., ge=0)
    unique_authors_14d: int = Field(..., ge=0)
    gini_14d: float = Field(..., ge=0, le=1)
    contributor_count_growth_7d: float
    conviction_bull_share: float | None = None
    conviction_researched_share: float | None = None
    conviction_entering_share: float | None = None
    conviction_exiting_share: float | None = None
    conviction_driver_top: str | None = None
    conviction_bull_researched_share: float | None = None
    conviction_bear_researched_share: float | None = None
    conviction_classified_14d: int | None = None


class NarrativeClusterOut(BaseModel):
    narrative_id: UUID
    label: str
    associated_tickers: list[str]
    lifecycle_stage: int = Field(..., ge=1, le=6)
    stage_confidence: float = Field(..., ge=0, le=1)
    velocity_14d: float
    cross_sub_count: int
    top_terms: list[str]
    first_seen_utc: datetime
    last_updated_utc: datetime


class NarrativeAlertOut(BaseModel):
    ticker: str
    alert_type: str
    triggered_at: datetime
    payload: dict[str, object]


# ---------- Conversion helpers ----------


def _acs_to_out(score: AcsScore) -> AcsScoreOut:
    return AcsScoreOut(
        ticker=score.ticker,
        scored_at=score.scored_at,
        acs=score.acs,
        acs_ci_lower=score.acs_ci_lower,
        acs_ci_upper=score.acs_ci_upper,
        components=AcsComponentsOut(
            a_attention_persistence=score.components.a_attention_persistence,
            b_contributor_quality=score.components.b_contributor_quality,
            c_narrative_strength=score.components.c_narrative_strength,
            d_thesis_quality=score.components.d_thesis_quality,
            e_market_confirmation=score.components.e_market_confirmation,
        ),
        dominant_signal=score.dominant_signal,
        decay_acs=score.decay_acs,
        flags=list(score.flags),
        lifecycle_stage=score.lifecycle_stage,
        stage_confidence=score.stage_confidence,
        stage_streak_days=score.stage_streak_days,
        first_emerged_at=score.first_emerged_at,
        acs_slope_14d=score.acs_slope_14d,
    )


def _detail_to_out(detail: TickerDetail) -> TickerDetailOut:
    return TickerDetailOut(
        ticker=detail.ticker,
        bucket_date=detail.bucket_date,
        score=_acs_to_out(detail.score),
        daily_buckets=[
            DailyBucketOut(day=b.day, count=b.count, unique_authors=b.unique_authors)
            for b in detail.daily_buckets
        ],
        tier1_pct=detail.tier1_pct,
        tier2_pct=detail.tier2_pct,
        tier3_pct=detail.tier3_pct,
        mentions_14d=detail.mentions_14d,
        unique_authors_14d=detail.unique_authors_14d,
        gini_14d=detail.gini_14d,
        contributor_count_growth_7d=detail.contributor_count_growth_7d,
        conviction_bull_share=detail.conviction_bull_share,
        conviction_researched_share=detail.conviction_researched_share,
        conviction_entering_share=detail.conviction_entering_share,
        conviction_exiting_share=detail.conviction_exiting_share,
        conviction_driver_top=detail.conviction_driver_top,
        conviction_bull_researched_share=detail.conviction_bull_researched_share,
        conviction_bear_researched_share=detail.conviction_bear_researched_share,
        conviction_classified_14d=detail.conviction_classified_14d,
    )


def _cluster_to_out(cluster: NarrativeCluster) -> NarrativeClusterOut:
    return NarrativeClusterOut(
        narrative_id=cluster.narrative_id,
        label=cluster.label,
        associated_tickers=list(cluster.associated_tickers),
        lifecycle_stage=cluster.lifecycle_stage,
        stage_confidence=cluster.stage_confidence,
        velocity_14d=cluster.velocity_14d,
        cross_sub_count=cluster.cross_sub_count,
        top_terms=list(cluster.top_terms),
        first_seen_utc=cluster.first_seen_utc,
        last_updated_utc=cluster.last_updated_utc,
    )


def _alert_to_out(alert: NarrativeAlert) -> NarrativeAlertOut:
    return NarrativeAlertOut(
        ticker=alert.ticker,
        alert_type=alert.alert_type,
        triggered_at=alert.triggered_at,
        payload=dict(alert.payload),
    )


def _map_service_error(exc: Exception) -> HTTPException:
    """Translate narrative-domain exceptions to HTTP errors."""
    if isinstance(exc, NarrativeUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, TickerNotTracked):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, NarrativeNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    logger.exception("Unhandled narrative service error: %s", exc)
    return HTTPException(status_code=500, detail="Internal narrative service error")


# ---------- Routes ----------


@router.get("/tickers/{ticker}/acs", response_model=AcsScoreOut)
@limiter.limit("30/minute")
async def get_acs(
    request: Request,
    ticker: Annotated[str, Path(min_length=1, max_length=10, pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")],
) -> AcsScoreOut:
    try:
        score = await read_service.get_acs_for_ticker(ticker.upper())
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return _acs_to_out(score)


@router.get("/tickers/{ticker}/detail", response_model=TickerDetailOut)
@limiter.limit("30/minute")
async def get_ticker_detail(
    request: Request,
    ticker: Annotated[str, Path(min_length=1, max_length=10, pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")],
) -> TickerDetailOut:
    try:
        detail = await read_service.get_ticker_detail(ticker.upper())
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return _detail_to_out(detail)


@router.get("/tickers/top", response_model=list[AcsScoreOut])
@limiter.limit("30/minute")
async def get_top(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AcsScoreOut]:
    try:
        rows = await read_service.get_top_tickers(limit=limit)
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return [_acs_to_out(r) for r in rows]


@router.get("/emerging", response_model=list[AcsScoreOut])
@limiter.limit("30/minute")
async def get_emerging(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AcsScoreOut]:
    try:
        rows = await read_service.get_emerging_tickers(limit=limit)
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return [_acs_to_out(r) for r in rows]


@router.get("/narratives/{narrative_id}", response_model=NarrativeClusterOut)
@limiter.limit("30/minute")
async def get_narrative_cluster(
    request: Request,
    narrative_id: UUID,
) -> NarrativeClusterOut:
    try:
        cluster = await read_service.get_narrative(narrative_id)
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return _cluster_to_out(cluster)


@router.get("/alerts", response_model=list[NarrativeAlertOut])
@limiter.limit("30/minute")
async def get_alerts(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NarrativeAlertOut]:
    try:
        rows = await read_service.get_alerts(limit=limit)
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return [_alert_to_out(r) for r in rows]
