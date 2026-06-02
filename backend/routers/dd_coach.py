"""DD Coach routes — thin CRUD + Phase 1 read/compute endpoints.

Endpoints (all rate-limited via slowapi):
  CRUD (Phase 0):
    POST   /api/dd_coach/entries
    GET    /api/dd_coach/entries
    GET    /api/dd_coach/entries/{id}
    PATCH  /api/dd_coach/entries/{id}
    POST   /api/dd_coach/entries/{id}/complete
    DELETE /api/dd_coach/entries/{id}

  Phase 1 — data + valuation:
    GET    /api/dd_coach/data_card/{ticker}
    GET    /api/dd_coach/filings/{ticker}
    POST   /api/dd_coach/valuation

Layering (copilot-instructions.md): this router validates, delegates, and
converts only. All business logic lives in services/dd_coach/*.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from limiter import limiter
from services.dd_coach import (
    data_card_service,
    entry_service,
    filings_service,
    path_to_target_service,
    valuation_service,
)
from services.dd_coach.errors import (
    DDCoachUnavailable,
    DDEntryImmutable,
    DDEntryInvalid,
    DDEntryNotFound,
)
from services.dd_coach.filings_intel import service as filings_intel_service
from services.dd_coach.filings_intel.prompts import VALID_INSIGHT_TYPES
from services.dd_coach.models import (
    CreateEntryInput,
    DDEntryDoc,
    EntryStatus,
    PatchEntryInput,
    ValuationMethod,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dd_coach", tags=["dd_coach"])


# ---------- Response envelope ----------------------------------------------


class EntryOut(BaseModel):
    """API response shape — currently a passthrough of DDEntryDoc."""

    model_config = ConfigDict(extra="ignore")

    id: str
    ticker: str
    user_id: str
    created_at: str
    updated_at: str
    completed_at: str | None
    status: str
    data_card_snapshot: dict[str, Any]
    answers: dict[str, Any]
    valuation: dict[str, Any]
    sizing: dict[str, Any]


class EntryListOut(BaseModel):
    items: list[EntryOut]
    count: int


def _to_out(entry: DDEntryDoc) -> EntryOut:
    return EntryOut.model_validate(entry.model_dump(mode="json"))


# ---------- Exception → HTTP mapping ---------------------------------------


def _raise_http(exc: Exception) -> None:
    """Map a DD Coach domain exception to HTTPException."""
    if isinstance(exc, DDEntryNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DDEntryImmutable):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DDEntryInvalid):
        raise HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, DDCoachUnavailable):
        raise HTTPException(status_code=503, detail=str(exc))
    raise exc


# ---------- Routes ----------------------------------------------------------


@router.post("/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def create_entry(request: Request, payload: CreateEntryInput) -> EntryOut:
    try:
        entry = entry_service.create_draft(payload.ticker)
    except DDCoachUnavailable as exc:
        _raise_http(exc)
    return _to_out(entry)


@router.get("/entries", response_model=EntryListOut)
@limiter.limit("60/minute")
def list_entries(
    request: Request,
    ticker: str | None = Query(default=None, max_length=10),
    status_filter: EntryStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> EntryListOut:
    try:
        entries = entry_service.list_entries(
            ticker=ticker,
            status=status_filter,
            limit=limit,
        )
    except DDCoachUnavailable as exc:
        _raise_http(exc)
    items = [_to_out(e) for e in entries]
    return EntryListOut(items=items, count=len(items))


@router.get("/entries/{entry_id}", response_model=EntryOut)
@limiter.limit("60/minute")
def get_entry(
    request: Request,
    entry_id: str = Path(..., min_length=1, max_length=64),
    ticker: str = Query(..., min_length=1, max_length=10),
) -> EntryOut:
    try:
        entry = entry_service.get_entry(entry_id, ticker)
    except (DDEntryNotFound, DDCoachUnavailable) as exc:
        _raise_http(exc)
    return _to_out(entry)


@router.patch("/entries/{entry_id}", response_model=EntryOut)
@limiter.limit("60/minute")
def patch_entry(
    request: Request,
    payload: PatchEntryInput,
    entry_id: str = Path(..., min_length=1, max_length=64),
    ticker: str = Query(..., min_length=1, max_length=10),
) -> EntryOut:
    try:
        entry = entry_service.patch_entry(entry_id, ticker, payload)
    except (DDEntryNotFound, DDEntryImmutable, DDCoachUnavailable) as exc:
        _raise_http(exc)
    return _to_out(entry)


@router.post("/entries/{entry_id}/complete", response_model=EntryOut)
@limiter.limit("30/minute")
def complete_entry(
    request: Request,
    entry_id: str = Path(..., min_length=1, max_length=64),
    ticker: str = Query(..., min_length=1, max_length=10),
) -> EntryOut:
    try:
        entry = entry_service.complete_entry(entry_id, ticker)
    except (
        DDEntryNotFound,
        DDEntryImmutable,
        DDEntryInvalid,
        DDCoachUnavailable,
    ) as exc:
        _raise_http(exc)
    return _to_out(entry)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
def delete_entry(
    request: Request,
    entry_id: str = Path(..., min_length=1, max_length=64),
    ticker: str = Query(..., min_length=1, max_length=10),
) -> None:
    try:
        entry_service.delete_entry(entry_id, ticker)
    except (DDEntryNotFound, DDEntryImmutable, DDCoachUnavailable) as exc:
        _raise_http(exc)
    return None


# ---------------------------------------------------------------------------
# Phase 1 — Data Card
# ---------------------------------------------------------------------------


@router.get("/data_card/{ticker}")
@limiter.limit("30/minute")
def get_data_card(
    request: Request,
    ticker: str = Path(..., min_length=1, max_length=10),
) -> dict[str, Any]:
    """Return the read-only Screen-1 snapshot for a ticker."""
    try:
        card = data_card_service.get_data_card(ticker)
    except (DDEntryNotFound, DDCoachUnavailable) as exc:
        _raise_http(exc)
    return card.to_dict()


# ---------------------------------------------------------------------------
# Phase 2 — Path to Target (Screen 6)
# ---------------------------------------------------------------------------


@router.get("/path_to_target/{ticker}")
@limiter.limit("30/minute")
def get_path_to_target(
    request: Request,
    ticker: str = Path(..., min_length=1, max_length=10),
    target_price: float = Query(..., gt=0, description="User-entered target price"),
) -> dict[str, Any]:
    """Return three paths (growth / multiple / mixed) from spot to ``target_price``."""
    try:
        result = path_to_target_service.get_path_to_target(ticker, target_price)
    except (DDEntryNotFound, DDEntryInvalid, DDCoachUnavailable) as exc:
        _raise_http(exc)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Phase 1 — Filings
# ---------------------------------------------------------------------------


@router.get("/filings/{ticker}")
@limiter.limit("60/minute")
def get_filings(
    request: Request,
    ticker: str = Path(..., min_length=1, max_length=10),
) -> dict[str, str]:
    """Return SEC EDGAR landing-page URLs for the common DD filings."""
    try:
        links = filings_service.get_filing_links(ticker)
    except DDEntryNotFound as exc:
        _raise_http(exc)
    return links.to_dict()


# ---------------------------------------------------------------------------
# Phase 3 — LLM filings intelligence
# ---------------------------------------------------------------------------


_INSIGHT_TYPE_LITERAL = Literal[
    "business_summary",
    "risk_diff",
    "mda_summary",
    "leadership",
    "bear_scaffold",
]


@router.get("/intel/{ticker}/{insight_type}")
@limiter.limit("10/minute")
def get_filings_intel(
    request: Request,
    ticker: str = Path(..., min_length=1, max_length=10),
    insight_type: _INSIGHT_TYPE_LITERAL = Path(..., description="One of: " + ", ".join(VALID_INSIGHT_TYPES)),
    force: bool = Query(default=False, description="Bypass cache and recompute"),
) -> dict[str, Any]:
    """Return LLM-derived insight for a filing.

    First call per (ticker, accession#, insight_type) hits the SEC + Azure
    OpenAI; subsequent calls return the cached Cosmos document. Set
    ``force=true`` to recompute.
    """
    try:
        result = filings_intel_service.get_intel(ticker, insight_type, force=force)
    except (DDEntryNotFound, DDEntryInvalid, DDCoachUnavailable) as exc:
        _raise_http(exc)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Phase 1 — Valuation
# ---------------------------------------------------------------------------


class MultipleBasedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    forward_eps: float
    target_pe_low: float = Field(..., gt=0)
    target_pe_mid: float = Field(..., gt=0)
    target_pe_high: float = Field(..., gt=0)
    spot_price: float | None = None


class MaturityDiscountRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    revenue_bear: float = Field(..., ge=0)
    revenue_base: float = Field(..., ge=0)
    revenue_bull: float = Field(..., ge=0)
    mature_multiple: float = Field(..., gt=0)
    shares_outstanding_today: float = Field(..., gt=0)
    spot_price: float | None = None
    years_to_maturity: int = Field(
        valuation_service.DEFAULT_YEARS_TO_MATURITY, gt=0, le=20,
    )
    dilution_pct: float = Field(
        valuation_service.DEFAULT_DILUTION_PCT, ge=0, lt=5,
    )
    discount_rate: float = Field(
        valuation_service.DEFAULT_DISCOUNT_RATE, gt=0, lt=1,
    )


class ValuationRequest(BaseModel):
    """Discriminated by ``method``. Exactly one of the *_inputs blocks must be
    populated for ``multiple_based`` / ``maturity_discount``; ``optionality``
    needs only the optional spot price."""

    model_config = ConfigDict(extra="ignore")

    method: ValuationMethod
    spot_price: float | None = None
    multiple_based: MultipleBasedRequest | None = None
    maturity_discount: MaturityDiscountRequest | None = None


@router.post("/valuation")
@limiter.limit("60/minute")
def compute_valuation(
    request: Request,
    payload: ValuationRequest,
) -> dict[str, Any]:
    """Compute bear/base/bull per-share using the selected method."""
    try:
        if payload.method is ValuationMethod.MULTIPLE_BASED:
            if payload.multiple_based is None:
                raise DDEntryInvalid("multiple_based payload required for method=multiple_based.")
            mb = payload.multiple_based
            out = valuation_service.compute_multiple_based(
                valuation_service.MultipleBasedInputs(
                    forward_eps=mb.forward_eps,
                    target_pe_low=mb.target_pe_low,
                    target_pe_mid=mb.target_pe_mid,
                    target_pe_high=mb.target_pe_high,
                    spot_price=mb.spot_price or payload.spot_price,
                ),
            )
        elif payload.method is ValuationMethod.MATURITY_DISCOUNT:
            if payload.maturity_discount is None:
                raise DDEntryInvalid(
                    "maturity_discount payload required for method=maturity_discount.",
                )
            md = payload.maturity_discount
            out = valuation_service.compute_maturity_discount(
                valuation_service.MaturityDiscountInputs(
                    revenue_bear=md.revenue_bear,
                    revenue_base=md.revenue_base,
                    revenue_bull=md.revenue_bull,
                    mature_multiple=md.mature_multiple,
                    shares_outstanding_today=md.shares_outstanding_today,
                    spot_price=md.spot_price or payload.spot_price,
                    years_to_maturity=md.years_to_maturity,
                    dilution_pct=md.dilution_pct,
                    discount_rate=md.discount_rate,
                ),
            )
        else:
            out = valuation_service.compute_optionality(spot_price=payload.spot_price)
    except DDEntryInvalid as exc:
        _raise_http(exc)
    return out.to_dict()
