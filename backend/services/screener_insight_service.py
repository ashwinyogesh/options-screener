"""
LLM-powered trade insight for a single CSP screener row — v2 regime-aware.

Produces cycle-adjusted value bands (Bear / Normal / Bull) for any ticker,
evaluates the CSP strike against those bands, and applies a VIX × cycle
matrix gate before issuing ENTER / WAIT / SKIP.

Enriches each request with company profile + VIX regime from data_service
before calling Azure OpenAI. The LLM is instructed not to hallucinate facts
beyond what it receives.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Literal, Optional

from openai import AzureOpenAI

from services.data_service import get_news, get_ohlc, get_ticker_info

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
    earnings_within_dte: bool
    # screener scores kept for router back-compat but NOT sent to LLM
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str
    strike_detail: str
    roc_annualized: Optional[float]
    rsi: float
    iv_hv_ratio: Optional[float]
    iv_percentile: Optional[float]
    dist_from_52w_high_pct: float


@dataclass(frozen=True)
class InsightResult:
    reasoning: str         # chain-of-thought: business quality assessment (FIRST)
    verdict: Literal["ENTER", "WAIT", "SKIP"]
    confidence: float
    summary: str
    regime_drivers: str    # "BTC price + AI data center capex"
    current_regime: str    # "Mid-cycle — BTC ~$82K, recovering from Jan lows"
    stock_cycle: str       # "Bear" | "Normal" | "Bull"
    bear_band: str         # "$15–$35"
    normal_band: str       # "$40–$65"
    bull_band: str         # "$80+" or "$80–$120"
    ownership_case: str    # "Strike $50 below Normal floor — comfortable owning if mid-cycle holds"
    key_risk: str          # single sentence
    vix_regime: str        # "Calm" | "Normal" | "Elevated" | "Panic" | "Unknown"

class InsightError(Exception):
    """Raised when the insight call cannot be completed."""


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

_ENV_MAX = {"IVP": 35, "Tr": 15, "SMA": 5, "SLP": 5, "RSI": 20, "OI": 20}
_ENV_LABELS = {
    "IVP": "IV Percentile",
    "Tr":  "52W High Distance",
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
You are a value-oriented equity investor evaluating a Cash-Secured Put (CSP).

Your task: answer one question with independence and discipline.

  "If this put is assigned and I am holding 100 shares at a cost basis of the
   given strike, would I be comfortable holding that position for 6-12 months?"

Reason from business fundamentals and current market regime. You have NO
information about screener scores -- produce an independent ownership verdict.

Follow these six steps exactly:

STEP 1 -- REASON THROUGH THE BUSINESS
  From business (summary, sector, industry) and financials, assess:
  - Quality: is FCF positive? Is ROE above 10%? Is debt-to-equity manageable (< 2)?
  - Trajectory: growing, stable, or declining? (revenue_growth_pct > 0 = growing)
  - Valuation: stretched, fair, or cheap relative to sector norms?
    (trailing_pe vs forward_pe -- declining PE is a green flag; elevated PE with
     declining revenue is a red flag; None values mean data was unavailable)
  Write your working in 3-4 sentences covering quality, trajectory, and valuation.
  Output -> reasoning

STEP 2 -- IDENTIFY REGIME DRIVERS
  From business (sector, industry, summary) and recent_headlines,
  identify 1-2 primary external drivers that determine this stock's valuation cycle.
  Examples: "BTC price + AI capex", "consumer spending + commodity costs",
  "interest rates + credit spreads", "oil price + refining margins".
  Output -> regime_drivers (10 words max)

STEP 3 -- ASSESS CURRENT REGIME
  From recent_headlines and one_day_change_pct, classify the current cycle:
  Bear (stress / contraction), Normal (stable / ranging), or Bull (expansion / momentum).
  Briefly state why (one clause, e.g. "BTC ~$82K, recovering from Jan lows").
  Output -> current_regime (15 words max), stock_cycle (exactly one of: Bear, Normal, Bull)

STEP 4 -- PRODUCE FUNDAMENTAL VALUE BANDS
  Produce three non-overlapping dollar bands based on VALUATION SCENARIOS,
  not chart levels. Do NOT anchor on 52w_low.

  - bear_band: where the stock stabilises if revenue contracts 15-20% and the
    sector multiple compresses to distress levels. This is a fundamental
    distress valuation, not a technical support. Width <= 30% of normal_band width.
  - normal_band: fair value at current growth rate with the current sector
    multiple. Must satisfy: low < current_price < high (brackets current
    price). Width = 12-20% of current_price.
  - bull_band: where the stock trades if growth accelerates or the multiple
    expands. For enterprise / consumer / industrial names: bounded range,
    same approximate width as normal_band. Open-ended format ("$X+") ONLY
    for names whose primary driver has no fundamental ceiling (e.g.
    crypto-linked, pure commodity).

  Format: "$X-$Y" for bounded, "$X+" for open-ended. Integer dollar values only.
  The 52w_high and 52w_low in the payload are provided as context only -- do not
  use them as band anchors.
  Output -> bear_band, normal_band, bull_band

STEP 5 -- OWNERSHIP VERDICT
  Given cost basis = strike if assigned:
  - Does the strike sit below the normal_band floor? (good: buffer before
    assignment hurts)
  - Would you hold 100 shares at this cost basis for 6-12 months given the
    business quality and current regime?
  One sentence on whether the ownership case is sound.
  Output -> ownership_case (25 words max)

STEP 6 -- VERDICT via VIX x cycle matrix
  Use the following gate:

  stock_cycle / vix_regime |  Calm   Normal  Elevated  Panic
  -------------------------+--------------------------------------
  Bear                     |  SKIP   SKIP    WAIT      SKIP
  Normal                   |  WAIT   ENTER   ENTER     WAIT
  Bull                     |  ENTER  ENTER   ENTER     WAIT

  Override rules (applied after the matrix):
  - strike > normal_band ceiling -> always SKIP
  - earnings_within_dte is true AND stock_cycle is Bear -> always SKIP
  - poor business quality (FCF negative AND debt_to_equity > 3 AND
    revenue_growth_pct < 0) -> WAIT minimum, never ENTER
  - WAIT is preferred over SKIP when regime is uncertain or data is thin

  Output -> verdict (ENTER | WAIT | SKIP)

Output rules:
  - Reason ONLY from data provided. Do not invent or assume facts.
  - If fundamental data fields are None, state that data was unavailable and
    rely on qualitative description from business_summary.
  - summary: 2-3 sentences on the ownership case + verdict rationale.
    Do not mention screener scores -- they were not provided to you.
  - key_risk: one sentence -- the specific scenario that would make you regret
    this assignment.
  - confidence: 0.0-1.0 reflecting how clearly the data supports the verdict.
    Lower confidence when multiple fundamental fields are None.
"""

def _build_user_prompt(req: InsightRequest, one_day_change_pct: Optional[float], news: list[dict], ticker_profile: dict) -> str:
    payload = {
        "trade": {
            "symbol": req.symbol,
            "strike": req.strike,
            "premium": req.premium,
            "dte": req.dte,
            "expiration": req.expiration,
            "breakeven": round(req.strike - req.premium, 2),
            "otm_pct": round((req.strike - req.price) / req.price * 100, 1) if req.price else None,
            "earnings_within_dte": req.earnings_within_dte,
            "current_price": req.price,
        },
        "business": {
            "summary": ticker_profile.get("business_summary"),
            "sector": ticker_profile.get("sector"),
            "industry": ticker_profile.get("industry"),
            "trailing_pe": ticker_profile.get("trailing_pe"),
            "forward_pe": ticker_profile.get("forward_pe"),
            "revenue_growth_pct": ticker_profile.get("revenue_growth_pct"),
            "free_cashflow_b": ticker_profile.get("free_cashflow_b"),
            "debt_to_equity": ticker_profile.get("debt_to_equity"),
            "return_on_equity_pct": ticker_profile.get("return_on_equity_pct"),
            "52w_high": ticker_profile.get("52w_high"),
            "52w_low": ticker_profile.get("52w_low"),
        },
        "market_context": {
            "vix": ticker_profile.get("vix_current"),
            "vix_regime": ticker_profile.get("vix_regime", "Unknown"),
            "one_day_change_pct": one_day_change_pct,
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
        "required": [
            "reasoning",
            "verdict", "confidence", "summary",
            "regime_drivers", "current_regime", "stock_cycle",
            "bear_band", "normal_band", "bull_band",
            "ownership_case", "key_risk",
        ],
        "properties": {
            "reasoning":      {"type": "string"},
            "verdict":        {"type": "string", "enum": ["ENTER", "WAIT", "SKIP"]},
            "confidence":     {"type": "number"},
            "summary":        {"type": "string"},
            "regime_drivers": {"type": "string"},
            "current_regime": {"type": "string"},
            "stock_cycle":    {"type": "string", "enum": ["Bear", "Normal", "Bull"]},
            "bear_band":      {"type": "string"},
            "normal_band":    {"type": "string"},
            "bull_band":      {"type": "string"},
            "ownership_case": {"type": "string"},
            "key_risk":       {"type": "string"},
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
    ticker_profile = get_ticker_info(req.symbol)

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
                {"role": "user", "content": _build_user_prompt(req, one_day_change, news, ticker_profile)},
            ],
            response_format={"type": "json_schema", "json_schema": _RESPONSE_SCHEMA},
            temperature=0.3,
            max_tokens=1200,
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

    return InsightResult(
        reasoning=str(data.get("reasoning", "")),
        verdict=verdict,
        confidence=float(max(0.0, min(1.0, data.get("confidence", 0.5)))),
        summary=str(data.get("summary", "")),
        regime_drivers=str(data.get("regime_drivers", "")),
        current_regime=str(data.get("current_regime", "")),
        stock_cycle=str(data.get("stock_cycle", "Normal")),
        bear_band=str(data.get("bear_band", "")),
        normal_band=str(data.get("normal_band", "")),
        bull_band=str(data.get("bull_band", "")),
        ownership_case=str(data.get("ownership_case", "")),
        key_risk=str(data.get("key_risk", "")),
        vix_regime=str(ticker_profile.get("vix_regime", "Unknown")),
    )
