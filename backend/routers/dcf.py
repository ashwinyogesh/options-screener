"""DCF valuation router."""
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from limiter import limiter
from services.dcf_service import get_dcf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dcf", tags=["dcf"])


@router.get("")
@limiter.limit("5/minute")
def fetch(
    request: Request,
    ticker: str = Query(..., min_length=1, max_length=10, pattern=r"^[A-Za-z\.\-]+$"),
    refresh: bool = Query(False, description="Skip cache and re-run analysis"),
    trials: int = Query(5000, ge=500, le=20000, description="Monte Carlo trials"),
) -> dict:
    try:
        return get_dcf(ticker, refresh=refresh, trials=trials)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("DCF failed for %s", ticker)
        raise HTTPException(status_code=500, detail=str(e))
