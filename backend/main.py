"""
CSP Screener — FastAPI application entry point.
Run: uvicorn main:app --reload --port 8000
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.screener import router as screener_router
from routers.ditm import router as ditm_router
from routers.momentum import router as momentum_router
from routers.cc import router as cc_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="CSP Screener API",
    description="Cash Secured Put screener using technical + options signals.",
    version="1.0.0",
)

# Allow the Vite dev server and any localhost port during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://options.tinkerhub.xyz",
        "https://optionsapi-ajdwhug5g9ena5bj.centralus-01.azurewebsites.net",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(screener_router)
app.include_router(ditm_router)
app.include_router(momentum_router)
app.include_router(cc_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
