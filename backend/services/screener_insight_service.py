"""
LLM-powered trade insight for a single CSP screener row.

Fetches recent news via data_service, computes 1-day return from OHLC,
then calls Azure OpenAI to produce a structured ENTER / WAIT / SKIP verdict.

The insight is grounded only in data that is explicitly passed to the prompt —
the LLM is instructed not to hallucinate facts beyond what it receives.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Literal, Optional

from openai import AzureOpenAI

from services.data_service import get_news, get_ohlc

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Azure OpenAI config — same env vars as dcf_service / llm_extractor
# ---------------------------------------------------------------------------
_AZURE_KEY = os.getenv("AZURE_OPENAI_KEY", "")
_AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
_AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InsightRequest:
    symbol: str
    price: float
    strike: float
    premium: float
    dte: int
    expiration: str
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str        # e.g. "IH:12 Tr:15 SMA:0 SLP:0 RSI:0 OI:18"
    strike_detail: str     # e.g. "Δ:20 BA:24 LQ:15 ROC:35"
    roc_annualized: Optional[float]
    rsi: float
    iv_hv_ratio: Optional[float]
    dist_from_52w_high_pct: float


@dataclass(frozen=True)
class InsightResult:
    verdict: Literal["ENTER", "WAIT", "SKIP"]
    confidence: float
    summary: str
    env_flag: str
    strike_flag: str
    key_risk: str
    reentry_condition: Optional[str]


class InsightError(Exception):
    """Raised when the insight call cannot be completed."""


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

_ENV_MAX = {"IH": 35, "Tr": 15, "SMA": 5, "SLP": 5, "RSI": 20, "OI": 20}
_ENV_LABELS = {
    "IH": "IV/HV Ratio",
    "Tr": "52W High Distance",
    "SMA": "SMA50/200 Alignment",
    "SLP": "SMA50 10d Slope",
    "RSI": "RSI(14)",
    "OI": "Chain Liquidity",
}
_STRIKE_MAX = {"Δ": 25, "BA": 25, "LQ": 15, "ROC": 35}
_STRIKE_LABELS = {
    "Δ": "Delta Position",
    "BA": "Bid-Ask Spread",
    "LQ": "Strike Liquidity",
    "ROC": "Annualised Return",
}


def _parse_detail(detail: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in detail.split():
        idx = part.find(":")
        if idx > 0:
            try:
                out[part[:idx]] = float(part[idx + 1:])
            except ValueError:
                pass
    return out


def _format_factors(detail: str, max_map: dict[str, int], label_map: dict[str, str]) -> list[dict]:
    pts = _parse_detail(detail)
    result = []
    for key, max_val in max_map.items():
        earned = pts.get(key, 0.0)
        result.append({
            "factor": label_map.get(key, key),
            "earned": round(earned),
            "max": max_val,
            "pct": round(earned / max_val * 100) if max_val else 0,
        })
    return result


def _compute_1d_change(symbol: str) -> Optional[float]:
    """Returns today's 1-day % price change, or None on failure."""
    try:
        df = get_ohlc(symbol, period="5d")
        if len(df) < 2:
            return None
        prev = float(df["Close"].iloc[-2])
        curr = float(df["Close"].iloc[-1])
        if prev == 0:
            return None
        return round((curr / prev - 1) * 100, 2)
    except Exception as exc:
        logger.warning("1d change fetch failed for %s: %s", symbol, exc)
        return None


_SYSTEM_PROMPT = """\
You are an expert options trader specialising in Cash-Secured Puts (CSP).

You receive a scored CSP trade candidate plus recent news headlines. Your task is to
interpret the score breakdown in the context of current news and produce a clear verdict.

Scoring model reference:
  ENV score (0-100, weight 40%):
    IH  IV/HV Ratio        35 pts  seller's edge; ≥1.3× = full
    Tr  52W High Distance  15 pts  CSP: ≤5% below 52W high = full
    SMA SMA50/200 Align     5 pts  structural trend confirmation
    SLP SMA50 10d Slope     5 pts  momentum continuation
    RSI RSI(14)            20 pts  sweet spot 42-62; >75 = 0 (overbought)
    OI  Chain Liquidity    20 pts  log circuit-breaker
    Earnings penalty: -15 if earnings within DTE window
  Strike score (0-100, weight 60%):
    Δ   Delta Position     25 pts  ideal -0.225
    BA  Bid-Ask Spread     25 pts  ≤1% = full
    LQ  Strike Liquidity   15 pts  OI/volume circuit-breaker
    ROC Annualised Return  35 pts  ≥12% annualised = full
  Final = 0.4 × ENV + 0.6 × Strike

Verdict guide (use judgment, not rigid thresholds):
  ENTER — both ENV and Strike support entry; no significant timing or news risk
  WAIT  — mechanics are good but timing is off; better entry likely in coming days
           (common causes: gap-up today, RSI overbought, event-driven IV, catalyst that may reverse)
  SKIP  — structural problem in score or news makes this trade inadvisable

Rules:
- Reason ONLY from data and headlines you are given. Do not invent or assume facts.
- If no headlines are provided, reason from scores and data alone.
- summary: 2-3 sentences max. env_flag and strike_flag: 1 sentence each.
- key_risk: single sentence — the one scenario that would cause maximum loss.
- reentry_condition: WAIT verdicts only — 1-2 sentences stating concrete re-entry triggers
  (e.g. specific price level to watch, RSI cooling below a threshold, post-earnings IV crush,
  a catalyst resolving, or a date range). Be specific. Set to null for ENTER and SKIP.
- confidence: 0.0-1.0 reflecting how clear the verdict is given available data.
"""


def _build_user_prompt(req: InsightRequest, one_day_change_pct: Optional[float], news: list[dict]) -> str:
    env_factors = _format_factors(req.env_detail, _ENV_MAX, _ENV_LABELS)
    strike_factors = _format_factors(req.strike_detail, _STRIKE_MAX, _STRIKE_LABELS)
    payload = {
        "symbol": req.symbol,
        "current_price": req.price,
        "one_day_change_pct": one_day_change_pct,
        "trade": {
            "strike": req.strike,
            "premium": req.premium,
            "dte": req.dte,
            "expiration": req.expiration,
            "breakeven": round(req.strike - req.premium, 2),
            "otm_pct": round((req.strike - req.price) / req.price * 100, 1) if req.price else None,
        },
        "scores": {
            "final": round(req.final_score, 1),
            "env": round(req.env_score, 1),
            "strike": round(req.strike_score, 1),
        },
        "env_factors": env_factors,
        "strike_factors": strike_factors,
        "supporting_data": {
            "rsi_14": round(req.rsi, 1) if not math.isnan(req.rsi) else None,
            "iv_hv_ratio": round(req.iv_hv_ratio, 3) if req.iv_hv_ratio is not None else None,
            "dist_from_52w_high_pct": round(req.dist_from_52w_high_pct, 1),
            "roc_annualized_pct": round(req.roc_annualized, 1) if req.roc_annualized is not None else None,
        },
        "recent_headlines": news,
    }
    return json.dumps(payload, indent=2)


_RESPONSE_SCHEMA = {
    "name": "screener_insight",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "confidence", "summary", "env_flag", "strike_flag", "key_risk", "reentry_condition"],
        "properties": {
            "verdict": {"type": "string", "enum": ["ENTER", "WAIT", "SKIP"]},
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
            "env_flag": {"type": "string"},
            "strike_flag": {"type": "string"},
            "key_risk": {"type": "string"},
            "reentry_condition": {"type": ["string", "null"]},
        },
    },
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_insight(req: InsightRequest) -> InsightResult:
    """
    Fetches news, computes 1d change, calls Azure OpenAI, returns InsightResult.
    Raises InsightError with a human-readable message on failure.
    """
    if not _AZURE_KEY or not _AZURE_ENDPOINT:
        raise InsightError(
            "Azure OpenAI is not configured. Set AZURE_OPENAI_KEY and "
            "AZURE_OPENAI_ENDPOINT in backend/.env"
        )

    news = get_news(req.symbol, max_age_hours=72, max_items=8)
    one_day_change = _compute_1d_change(req.symbol)

    client = AzureOpenAI(
        api_key=_AZURE_KEY,
        azure_endpoint=_AZURE_ENDPOINT,
        api_version=_AZURE_API_VERSION,
    )

    try:
        response = client.chat.completions.create(
            model=_AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(req, one_day_change, news)},
            ],
            response_format={"type": "json_schema", "json_schema": _RESPONSE_SCHEMA},
            temperature=0.3,
            max_tokens=600,
        )
    except Exception as exc:
        logger.exception("Azure OpenAI call failed for %s insight", req.symbol)
        raise InsightError(f"LLM call failed: {exc}") from exc

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Insight JSON parse failed for %s: %s — raw: %.200s", req.symbol, exc, raw)
        raise InsightError("LLM returned malformed JSON") from exc

    verdict = data.get("verdict", "WAIT")
    if verdict not in ("ENTER", "WAIT", "SKIP"):
        verdict = "WAIT"

    raw_reentry = data.get("reentry_condition")
    return InsightResult(
        verdict=verdict,
        confidence=float(max(0.0, min(1.0, data.get("confidence", 0.5)))),
        summary=str(data.get("summary", "")),
        env_flag=str(data.get("env_flag", "")),
        strike_flag=str(data.get("strike_flag", "")),
        key_risk=str(data.get("key_risk", "")),
        reentry_condition=str(raw_reentry) if raw_reentry is not None else None,
    )
