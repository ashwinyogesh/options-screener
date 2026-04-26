"""Supply chain router."""
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from limiter import limiter
from services.supply_chain_service import get_supply_chain

router = APIRouter(prefix="/api/supply-chain", tags=["supply-chain"])


class SupplyChainResponse(BaseModel):
    ticker: str
    company_name: str
    filing_date: str
    accession: str
    suppliers: list[dict]
    customers: list[dict]
    competitors: list[dict]
    summary: str
    cached: bool
    eight_k_count: int = 0
    eight_k_dates: list[str] = []
    segments: list[str] = []
    concentration_note: str = ""
    enrichment_used: list[str] = []


@router.get("", response_model=SupplyChainResponse)
@limiter.limit("3/minute")
def fetch(
    request: Request,
    ticker: str = Query(..., min_length=1, max_length=10, pattern=r"^[A-Za-z\.\-]+$"),
    refresh: bool = Query(False, description="Skip cache and re-extract"),
    enrich: str = Query(
        "filing+industry",
        description="Enrichment level: 'filing' (10-K/8-K only) or 'filing+industry' (default; adds LLM industry-knowledge pass)",
        pattern=r"^(filing|filing\+industry)$",
    ),
) -> SupplyChainResponse:
    enrich_industry = enrich == "filing+industry"
    try:
        graph = get_supply_chain(
            ticker, force_refresh=refresh, enrich_industry=enrich_industry
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supply chain extraction failed: {e}")
    return SupplyChainResponse(**asdict(graph))
