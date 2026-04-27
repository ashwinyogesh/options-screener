"""DCF valuation router."""
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from limiter import limiter
from services.dcf_service import (
    get_dcf,
    EQUITY_RISK_PREMIUM,
    DEFAULT_RISK_FREE,
    DEFAULT_PRETAX_COST_OF_DEBT,
    MIN_WACC,
    MAX_WACC,
    MC_TRIALS,
    HIGH_GROWTH_THRESHOLD,
    FORECAST_YEARS,
    FORECAST_YEARS_HIGH_GROWTH,
    _fetch_risk_free_rate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dcf", tags=["dcf"])


@router.get("/constants")
def constants() -> dict:
    """Model constants under quarterly review, plus live risk-free rate."""
    try:
        live_rf = _fetch_risk_free_rate()
    except Exception:
        live_rf = None
    return {
        "equity_risk_premium": EQUITY_RISK_PREMIUM,
        "default_risk_free": DEFAULT_RISK_FREE,
        "live_risk_free": live_rf,
        "default_pretax_cost_of_debt": DEFAULT_PRETAX_COST_OF_DEBT,
        "min_wacc": MIN_WACC,
        "max_wacc": MAX_WACC,
        "mc_trials_default": MC_TRIALS,
        "high_growth_threshold": HIGH_GROWTH_THRESHOLD,
        "forecast_years": FORECAST_YEARS,
        "forecast_years_high_growth": FORECAST_YEARS_HIGH_GROWTH,
        "last_reviewed": "2026-04",
        "erp_source": "Damodaran implied US ERP",
        "rf_source": "^TNX (CBOE 10-year Treasury Yield Index)",
        "kd_source": "BBB corporate spread + risk-free baseline",
    }


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
