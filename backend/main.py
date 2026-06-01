"""
CSP Screener — FastAPI application entry point.
Run: uvicorn main:app --reload --port 8000
"""
import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Load .env before importing routers (env vars must exist when modules init)
load_dotenv()

from limiter import limiter
from routers.cc import router as cc_router
from routers.csp import router as csp_router
from routers.dcf import router as dcf_router
from routers.dd_coach import router as dd_coach_router
from routers.ditm import router as ditm_router
from routers.etv import router as etv_router
from routers.narrative import router as narrative_router
from routers.supply_chain import router as supply_chain_router
from routers.swing import router as swing_router
from services.scoring.config import SCORING_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="CSP Screener API",
    description="Cash Secured Put screener using technical + options signals.",
    version=SCORING_VERSION,
)

# Rate limiting (slowapi). Per-IP limits; 429 returned when exceeded.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Allow the Vite dev server and any localhost port during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://options.tinkerhub.xyz",
        "https://optionsapi-ajdwhug5g9ena5bj.centralus-01.azurewebsites.net",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
    # Custom response headers the frontend reads explicitly. CORS hides any
    # response header not in the CORS-safelisted set unless listed here.
    expose_headers=["X-Scoreboard-Computed-At"],
)

app.include_router(csp_router)
app.include_router(cc_router)
app.include_router(ditm_router)
app.include_router(supply_chain_router)
app.include_router(dcf_router)
app.include_router(etv_router)
app.include_router(swing_router)
app.include_router(narrative_router)
app.include_router(dd_coach_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "scoring_version": SCORING_VERSION}
