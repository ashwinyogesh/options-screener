"""
Discounted Cash Flow (DCF) valuation + Monte Carlo simulation service.

Phase A (trade-grade) upgrade:
- WACC built from CAPM (rf from ^TNX, ERP=5.0%, β + D/E from yfinance) — NOT guessed by LLM.
- Buyback yield derived from share-count history; shares decay each year in the DCF math.
- Reverse DCF: solves for the revenue growth rate the market is implying at current price.
- Sensitivity matrix: 5×5 grid (WACC ±100bp × terminal_g ±50bp).
- Verdict block: BUY / HOLD / AVOID + suggested entry/exit + thesis-killer (LLM output).
- 1000-trial Monte Carlo runs in numpy; LLM never simulates.

Pipeline:
1. Fetch grounding (yfinance: price, shares, debt, beta, revenue, margins, share-count history,
   capex/D&A; ^TNX for risk-free rate). Backend computes CAPM WACC and buyback yield.
2. Single Azure OpenAI gpt-4.1 call with `response_format=json_schema`. The LLM is GIVEN the
   computed WACC and supplies a small `wacc_risk_adj_bps` (–100..+150) per scenario only.
3. Backend computes per-scenario fair values, reverse DCF, sensitivity matrix, Monte Carlo.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

import numpy as np
import yfinance as yf
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

# --------------------------------------------------------------- Config -----
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SEC = 24 * 60 * 60

FORECAST_YEARS = 5
MC_TRIALS = 5000
MC_TRIALS_MIN = 500
MC_TRIALS_MAX = 20000
HIST_BINS = 30
SAMPLE_DOWNSAMPLE = 200

# Damodaran-style constants (review quarterly).
EQUITY_RISK_PREMIUM = 0.050      # 5.0% — implied ERP for US equities, ~Apr 2026
DEFAULT_RISK_FREE = 0.045        # used if ^TNX fetch fails
DEFAULT_BETA = 1.0
DEFAULT_PRETAX_COST_OF_DEBT = 0.055
MIN_WACC = 0.05
MAX_WACC = 0.16

# Hard guardrails for LLM-supplied numbers.
RANGES = {
    "revenue_growth": (-0.20, 0.60),
    "operating_margin": (-0.20, 0.70),
    "terminal_growth": (0.00, 0.045),
    "capex_pct_revenue": (0.0, 0.30),
}
WACC_RISK_ADJ_BPS_RANGE = (-100, 150)  # -100bp .. +150bp around CAPM WACC

DistShape = Literal["normal", "triangular", "uniform"]


# ---------------------------------------------------------------- Models ----
@dataclass
class WaccBuildup:
    risk_free_rate: float
    equity_risk_premium: float
    beta: float
    cost_of_equity: float
    pretax_cost_of_debt: float
    after_tax_cost_of_debt: float
    weight_equity: float
    weight_debt: float
    wacc: float


@dataclass
class Grounding:
    ticker: str
    company_name: str
    current_price: float
    market_cap: Optional[float]
    shares_out: Optional[float]
    net_debt: Optional[float]
    total_debt: Optional[float]
    cash: Optional[float]
    beta: Optional[float]
    revenue_ttm: Optional[float]
    revenue_history: list[dict]
    revenue_cagr_5y: Optional[float]
    operating_margin_ttm: Optional[float]
    tax_rate: Optional[float]
    sector: Optional[str]
    industry: Optional[str]
    buyback_yield: Optional[float]      # 5y avg share-count change; negative = dilution
    share_history: list[dict]            # [{year:int, shares:float}]
    wacc_buildup: WaccBuildup
    as_of: str


@dataclass
class ScenarioAssumption:
    label: str                          # Conservative | Base | Optimistic
    revenue_growth: float
    operating_margin: float
    wacc_risk_adj_bps: int               # offset from CAPM WACC, in bps
    discount_rate: float                 # derived: wacc_capm + adj
    terminal_growth: float
    capex_pct_revenue: float
    rationale: dict
    strongest_driver: str
    narrative: str


@dataclass
class ScenarioResult:
    label: str
    fair_value_per_share: float
    upside_pct: float
    enterprise_value: float
    equity_value: float
    pv_of_fcfs: float
    pv_of_terminal: float


@dataclass
class MonteCarloResult:
    trials: int
    percentiles: dict
    mean: float
    std: float
    prob_above_current: float
    histogram: dict
    sample: list[float]


@dataclass
class ReverseDcfResult:
    """What revenue growth rate makes FV == current price (holding other Base inputs)?"""
    implied_revenue_growth: Optional[float]
    base_revenue_growth: float
    delta_vs_base: Optional[float]      # implied - base
    interpretation: str                  # "market expects HIGHER/LOWER growth than base"


@dataclass
class SensitivityCell:
    wacc: float
    terminal_growth: float
    fair_value_per_share: float


@dataclass
class SensitivityMatrix:
    wacc_axis: list[float]
    terminal_growth_axis: list[float]
    grid: list[list[float]]              # [wacc_idx][tg_idx] -> FV
    base_wacc: float
    base_terminal_growth: float


@dataclass
class Verdict:
    recommendation: Literal["STRONG_BUY", "BUY", "HOLD", "AVOID", "STRONG_AVOID"]
    suggested_entry_price: float
    suggested_exit_price: float
    confidence: float                    # 0..1
    key_assumption_to_monitor: str
    margin_of_safety_pct: float          # (P25 - current)/current — informational


@dataclass
class DcfResult:
    ticker: str
    grounding: Grounding
    scenarios: list[ScenarioAssumption]
    scenario_values: list[ScenarioResult]
    monte_carlo: MonteCarloResult
    distributions: dict
    reverse_dcf: ReverseDcfResult
    sensitivity: SensitivityMatrix
    verdict: Verdict
    risks: list[str]
    key_drivers: list[str]
    model: str
    cached: bool = False


# --------------------------------------------------------------- Utils ------
def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fetch_risk_free_rate() -> float:
    """13-week T-bill (^IRX) is too short-dated; use ^TNX (10y)."""
    try:
        t = yf.Ticker("^TNX")
        h = t.history(period="5d")
        if h is not None and not h.empty:
            r = float(h["Close"].iloc[-1]) / 100.0
            if 0.0 < r < 0.10:
                return r
    except Exception as exc:
        logger.warning("^TNX fetch failed: %s — using fallback %.3f", exc, DEFAULT_RISK_FREE)
    return DEFAULT_RISK_FREE


def _compute_buyback_yield(t: yf.Ticker) -> tuple[Optional[float], list[dict]]:
    """
    Derive average annual change in share count from the last available years.
    Positive = buybacks (shares declining). Negative = dilution.

    Uses `shares` (annual share count history) when available; falls back to None.
    """
    try:
        bs = t.balance_sheet  # quarterly/annual; 'Ordinary Shares Number' or similar
        if bs is None or bs.empty:
            return None, []
        # Try common row names
        candidates = ["Ordinary Shares Number", "Share Issued", "Common Stock"]
        row = None
        for c in candidates:
            if c in bs.index:
                row = bs.loc[c].dropna()
                break
        if row is None or row.empty:
            return None, []
        history: list[dict] = []
        for date, val in row.items():
            yr = getattr(date, "year", None)
            v = _safe_float(val)
            if yr and v:
                history.append({"year": int(yr), "shares": v})
        history.sort(key=lambda r: r["year"])
        if len(history) < 2:
            return None, history
        # Avg annual decline = 1 - (last/first)^(1/n)
        first = history[0]["shares"]
        last = history[-1]["shares"]
        n = history[-1]["year"] - history[0]["year"]
        if n <= 0 or first <= 0:
            return None, history
        annual_change = (last / first) ** (1.0 / n) - 1.0
        # Buyback yield = -annual_change (positive when shares shrink)
        return float(-annual_change), history
    except Exception as exc:
        logger.warning("Buyback yield fetch failed: %s", exc)
        return None, []


def _build_wacc(
    beta: Optional[float],
    rf: float,
    market_cap: Optional[float],
    total_debt: Optional[float],
    tax_rate: Optional[float],
    pretax_kd: float = DEFAULT_PRETAX_COST_OF_DEBT,
) -> WaccBuildup:
    b = beta if (beta is not None and 0.2 <= beta <= 3.0) else DEFAULT_BETA
    ke = rf + b * EQUITY_RISK_PREMIUM
    tr = tax_rate if tax_rate is not None else 0.21
    kd_at = pretax_kd * (1.0 - tr)
    e = market_cap or 0.0
    d = total_debt or 0.0
    total = e + d
    we = e / total if total > 0 else 1.0
    wd = d / total if total > 0 else 0.0
    wacc = we * ke + wd * kd_at
    wacc = max(MIN_WACC, min(MAX_WACC, wacc))
    return WaccBuildup(
        risk_free_rate=rf,
        equity_risk_premium=EQUITY_RISK_PREMIUM,
        beta=b,
        cost_of_equity=ke,
        pretax_cost_of_debt=pretax_kd,
        after_tax_cost_of_debt=kd_at,
        weight_equity=we,
        weight_debt=wd,
        wacc=wacc,
    )


# ----------------------------------------------------- Grounding (yfinance) -
def fetch_grounding(ticker: str) -> Grounding:
    t = yf.Ticker(ticker)
    info: dict = {}
    try:
        info = t.info or {}
    except Exception as exc:
        logger.warning("yfinance info failed for %s: %s", ticker, exc)

    price = _safe_float(info.get("currentPrice")) or _safe_float(info.get("regularMarketPrice"))
    if price is None:
        hist = t.history(period="5d")
        if hist is not None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
    if price is None:
        raise ValueError(f"No price data for ticker '{ticker}'")

    company_name = info.get("longName") or info.get("shortName") or ticker.upper()
    market_cap = _safe_float(info.get("marketCap"))
    shares_out = _safe_float(info.get("sharesOutstanding"))
    if shares_out is None and market_cap is not None and price:
        shares_out = market_cap / price
    total_debt = _safe_float(info.get("totalDebt")) or 0.0
    cash = _safe_float(info.get("totalCash")) or 0.0
    net_debt = total_debt - cash
    beta = _safe_float(info.get("beta"))
    revenue_ttm = _safe_float(info.get("totalRevenue"))
    op_margin = _safe_float(info.get("operatingMargins"))

    # Historical revenue
    revenue_history: list[dict] = []
    rev_cagr: Optional[float] = None
    try:
        fin = t.financials
        if fin is not None and not fin.empty and "Total Revenue" in fin.index:
            row = fin.loc["Total Revenue"].dropna()
            for date, val in row.items():
                yr = getattr(date, "year", None)
                v = _safe_float(val)
                if yr and v:
                    revenue_history.append({"year": int(yr), "revenue": v})
            revenue_history.sort(key=lambda r: r["year"])
            if len(revenue_history) >= 2:
                first = revenue_history[0]["revenue"]
                last = revenue_history[-1]["revenue"]
                n = revenue_history[-1]["year"] - revenue_history[0]["year"]
                if n > 0 and first > 0:
                    rev_cagr = (last / first) ** (1.0 / n) - 1.0
    except Exception as exc:
        logger.warning("Revenue history fetch failed for %s: %s", ticker, exc)

    # Effective tax rate
    tax_rate: Optional[float] = None
    try:
        fin = t.financials
        if fin is not None and "Tax Provision" in fin.index and "Pretax Income" in fin.index:
            tax = _safe_float(fin.loc["Tax Provision"].iloc[0])
            pretax = _safe_float(fin.loc["Pretax Income"].iloc[0])
            if tax is not None and pretax and pretax != 0:
                tr = tax / pretax
                if 0 <= tr <= 0.5:
                    tax_rate = tr
    except Exception:
        pass
    if tax_rate is None:
        tax_rate = 0.21

    # Buyback yield
    bb_yield, share_history = _compute_buyback_yield(t)
    if bb_yield is not None:
        # Clip to +/- 8%
        bb_yield = float(max(-0.08, min(0.08, bb_yield)))

    # WACC build-up
    rf = _fetch_risk_free_rate()
    wacc_bu = _build_wacc(
        beta=beta,
        rf=rf,
        market_cap=market_cap,
        total_debt=total_debt,
        tax_rate=tax_rate,
    )

    return Grounding(
        ticker=ticker.upper(),
        company_name=company_name,
        current_price=float(price),
        market_cap=market_cap,
        shares_out=shares_out,
        net_debt=net_debt,
        total_debt=total_debt,
        cash=cash,
        beta=beta,
        revenue_ttm=revenue_ttm,
        revenue_history=revenue_history,
        revenue_cagr_5y=rev_cagr,
        operating_margin_ttm=op_margin,
        tax_rate=tax_rate,
        sector=info.get("sector"),
        industry=info.get("industry"),
        buyback_yield=bb_yield,
        share_history=share_history,
        wacc_buildup=wacc_bu,
        as_of=time.strftime("%Y-%m-%d"),
    )


# ---------------------------------------------------------------- Prompt ----
_SYSTEM_PROMPT = """You are an expert valuation analyst reasoning in the style of Aswath Damodaran.

You receive grounded financial data for a public company. Backend has ALREADY computed:
- Risk-free rate (10y Treasury)
- WACC via CAPM (using yfinance β + D/E weights + ERP=5.0%)
- 5y buyback yield
You DO NOT supply WACC. You supply a small `wacc_risk_adj_bps` offset per scenario (–100bp..+150bp) reflecting scenario-specific risk (e.g. Optimistic might subtract 25–50bp for execution certainty; Conservative adds 50–100bp for stress).

Output STRICT JSON matching the supplied schema. No markdown, no prose outside JSON fields.

Stay within these decimal ranges:
    revenue_growth:    -0.20 .. 0.60
    operating_margin:  -0.20 .. 0.70
    terminal_growth:   0.00  .. 0.045  (<= long-run nominal GDP)
    capex_pct_revenue: 0.00  .. 0.30
    wacc_risk_adj_bps: -100  .. +150   (integer)

Discipline:
- Conservative < Base < Optimistic for revenue_growth and operating_margin.
- Conservative wacc_risk_adj_bps > Base > Optimistic.
- Anchor BASE-CASE numbers to historical data: cite the 5y revenue CAGR, TTM operating margin, and beta in your narrative.
- If your base-case revenue_growth is more than 300bp away from the 5y CAGR, justify why explicitly.
- Damodaran style: focus on competitive moat, reinvestment efficiency, capital-allocation track record, behavioral biases. Tie story to numbers.

Monte Carlo distributions you must supply:
- revenue_growth: normal(mean, sd). Mean ≈ Base. sd should be REALISTIC — at least ½ × |Optimistic − Conservative|, not ¼. Most stocks have wider true uncertainty than analysts admit.
- operating_margin: normal(mean, sd). sd ≥ ½ × |Optimistic − Conservative|.
- discount_rate: triangular(low, mode, high) — low=Optimistic-adjusted WACC, mode=Base WACC (=CAPM WACC), high=Conservative-adjusted. (You supply the bps offsets; backend converts.) Express the params here as ABSOLUTE wacc values you would assign — backend will reconcile.
- terminal_growth: uniform(low, high) — low=Conservative, high=Optimistic.
- capex_pct_revenue: normal(mean, sd).

Verdict (the most important block — this drives trading):
- recommendation: one of STRONG_BUY, BUY, HOLD, AVOID, STRONG_AVOID. Based on where current price sits vs your scenario distribution AND quality of the business.
- suggested_entry_price: price below which you'd buy aggressively (typically near P25 fair value, possibly with a 10–15% margin-of-safety discount for low-confidence names).
- suggested_exit_price: price at which thesis is "played out" (typically near P75 fair value).
- confidence: 0..1 — how sure are you of this verdict? Lower for cyclicals, opaque accounting, or weak grounding data. Higher for stable compounders with rich filings.
- key_assumption_to_monitor: the SINGLE variable whose disconfirmation would break the thesis (e.g. "iPhone unit growth turning negative", "AWS margin compression", "subscriber net adds decelerating").

Risks: 4–7 concise items. Key drivers: 3–5 items.
"""


def _build_user_prompt(g: Grounding) -> str:
    return json.dumps({
        "company_name": g.company_name,
        "ticker": g.ticker,
        "current_price": g.current_price,
        "market_cap": g.market_cap,
        "shares_out": g.shares_out,
        "net_debt": g.net_debt,
        "beta": g.beta,
        "revenue_ttm": g.revenue_ttm,
        "revenue_history": g.revenue_history,
        "revenue_cagr_5y": g.revenue_cagr_5y,
        "operating_margin_ttm": g.operating_margin_ttm,
        "tax_rate": g.tax_rate,
        "sector": g.sector,
        "industry": g.industry,
        "buyback_yield_5y": g.buyback_yield,
        "wacc_capm": g.wacc_buildup.wacc,
        "wacc_buildup": asdict(g.wacc_buildup),
        "as_of": g.as_of,
    }, indent=2, default=str)


_RESPONSE_SCHEMA = {
    "name": "dcf_assumptions",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenarios", "distributions", "verdict", "risks", "key_drivers"],
        "properties": {
            "scenarios": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "label", "revenue_growth", "operating_margin",
                        "wacc_risk_adj_bps", "terminal_growth", "capex_pct_revenue",
                        "rationale", "strongest_driver", "narrative",
                    ],
                    "properties": {
                        "label": {"type": "string", "enum": ["Conservative", "Base", "Optimistic"]},
                        "revenue_growth": {"type": "number"},
                        "operating_margin": {"type": "number"},
                        "wacc_risk_adj_bps": {"type": "integer"},
                        "terminal_growth": {"type": "number"},
                        "capex_pct_revenue": {"type": "number"},
                        "rationale": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "revenue_growth", "operating_margin", "discount_rate",
                                "terminal_growth", "capex_pct_revenue",
                            ],
                            "properties": {
                                "revenue_growth": {"type": "string"},
                                "operating_margin": {"type": "string"},
                                "discount_rate": {"type": "string"},
                                "terminal_growth": {"type": "string"},
                                "capex_pct_revenue": {"type": "string"},
                            },
                        },
                        "strongest_driver": {
                            "type": "string",
                            "enum": [
                                "revenue_growth", "operating_margin", "discount_rate",
                                "terminal_growth", "capex_pct_revenue",
                            ],
                        },
                        "narrative": {"type": "string"},
                    },
                },
            },
            "distributions": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "revenue_growth", "operating_margin", "discount_rate",
                    "terminal_growth", "capex_pct_revenue",
                ],
                "properties": {
                    "revenue_growth": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["shape", "params"],
                        "properties": {
                            "shape": {"type": "string", "enum": ["normal"]},
                            "params": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["mean", "sd"],
                                "properties": {"mean": {"type": "number"}, "sd": {"type": "number"}},
                            },
                        },
                    },
                    "operating_margin": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["shape", "params"],
                        "properties": {
                            "shape": {"type": "string", "enum": ["normal"]},
                            "params": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["mean", "sd"],
                                "properties": {"mean": {"type": "number"}, "sd": {"type": "number"}},
                            },
                        },
                    },
                    "discount_rate": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["shape", "params"],
                        "properties": {
                            "shape": {"type": "string", "enum": ["triangular"]},
                            "params": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["low", "mode", "high"],
                                "properties": {
                                    "low": {"type": "number"},
                                    "mode": {"type": "number"},
                                    "high": {"type": "number"},
                                },
                            },
                        },
                    },
                    "terminal_growth": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["shape", "params"],
                        "properties": {
                            "shape": {"type": "string", "enum": ["uniform"]},
                            "params": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["low", "high"],
                                "properties": {"low": {"type": "number"}, "high": {"type": "number"}},
                            },
                        },
                    },
                    "capex_pct_revenue": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["shape", "params"],
                        "properties": {
                            "shape": {"type": "string", "enum": ["normal"]},
                            "params": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["mean", "sd"],
                                "properties": {"mean": {"type": "number"}, "sd": {"type": "number"}},
                            },
                        },
                    },
                },
            },
            "verdict": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "recommendation", "suggested_entry_price", "suggested_exit_price",
                    "confidence", "key_assumption_to_monitor",
                ],
                "properties": {
                    "recommendation": {
                        "type": "string",
                        "enum": ["STRONG_BUY", "BUY", "HOLD", "AVOID", "STRONG_AVOID"],
                    },
                    "suggested_entry_price": {"type": "number"},
                    "suggested_exit_price": {"type": "number"},
                    "confidence": {"type": "number"},
                    "key_assumption_to_monitor": {"type": "string"},
                },
            },
            "risks": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
            "key_drivers": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
        },
    },
}


def _call_llm(g: Grounding) -> dict:
    if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT in backend/.env"
        )
    client = AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    try:
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(g)},
            ],
            temperature=0.2,
            response_format={"type": "json_schema", "json_schema": _RESPONSE_SCHEMA},
        )
    except Exception as exc:
        logger.warning("json_schema response_format failed (%s); falling back to json_object", exc)
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT + "\n\nReturn ONLY a JSON object."},
                {"role": "user", "content": _build_user_prompt(g)},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


# --------------------------------------------------- Validation / clipping --
def _clip(name: str, v: float) -> float:
    lo, hi = RANGES[name]
    return float(max(lo, min(hi, v)))


def _validate_assumptions(d: dict, wacc_capm: float) -> dict:
    """Clip out-of-range numbers; convert wacc_risk_adj_bps -> discount_rate; enforce invariants."""
    for sc in d.get("scenarios", []):
        for k in RANGES:
            if k in sc:
                sc[k] = _clip(k, float(sc[k]))
        # Convert risk-adjustment bps to absolute discount rate
        adj_bps = int(sc.get("wacc_risk_adj_bps", 0))
        adj_bps = max(WACC_RISK_ADJ_BPS_RANGE[0], min(WACC_RISK_ADJ_BPS_RANGE[1], adj_bps))
        sc["wacc_risk_adj_bps"] = adj_bps
        dr = wacc_capm + adj_bps / 10000.0
        dr = max(MIN_WACC, min(MAX_WACC, dr))
        sc["discount_rate"] = float(dr)
        if sc["terminal_growth"] >= sc["discount_rate"]:
            sc["terminal_growth"] = max(0.0, sc["discount_rate"] - 0.02)

    # Distribution clipping
    dists = d.get("distributions", {})
    for var in ("revenue_growth", "operating_margin", "capex_pct_revenue"):
        p = dists.get(var, {}).get("params", {})
        if "mean" in p:
            p["mean"] = _clip(var, float(p["mean"]))
        if "sd" in p:
            p["sd"] = max(1e-4, abs(float(p["sd"])))
    p = dists.get("discount_rate", {}).get("params", {})
    for k in ("low", "mode", "high"):
        if k in p:
            p[k] = float(max(MIN_WACC, min(MAX_WACC, float(p[k]))))
    if "low" in p and "high" in p and p["low"] > p["high"]:
        p["low"], p["high"] = p["high"], p["low"]
    if "mode" in p:
        p["mode"] = min(max(p["mode"], p.get("low", p["mode"])), p.get("high", p["mode"]))
    p = dists.get("terminal_growth", {}).get("params", {})
    for k in ("low", "high"):
        if k in p:
            p[k] = _clip("terminal_growth", float(p[k]))
    if "low" in p and "high" in p and p["low"] > p["high"]:
        p["low"], p["high"] = p["high"], p["low"]
    return d


# ---------------------------------------------------------- DCF math --------
def _compute_dcf(
    revenue_ttm: float,
    revenue_growth: float,
    operating_margin: float,
    discount_rate: float,
    terminal_growth: float,
    capex_pct_revenue: float,
    tax_rate: float,
    shares_out: float,
    net_debt: float,
    buyback_yield: float = 0.0,
    forecast_years: int = FORECAST_YEARS,
) -> dict:
    """
    FCFF DCF with explicit revenue-growth fade and share-count attrition from buybacks.
    FCFF_t = NOPAT_t - capex_growth_t  (D&A ≈ maintenance capex assumption).
    Terminal value via Gordon Growth.

    `buyback_yield` is the annual % decline in share count (positive = buybacks).
    Per-share fair value uses post-attrition share count (snapshot at year `forecast_years`).
    """
    if discount_rate <= terminal_growth:
        discount_rate = terminal_growth + 0.01

    fcfs = []
    pvs = []
    rev = revenue_ttm
    for yr in range(1, forecast_years + 1):
        g = revenue_growth + (terminal_growth - revenue_growth) * (yr - 1) / max(1, forecast_years - 1)
        rev = rev * (1.0 + g)
        ebit = rev * operating_margin
        nopat = ebit * (1.0 - tax_rate)
        capex_growth = rev * capex_pct_revenue
        fcf = nopat - capex_growth
        fcfs.append(fcf)
        pvs.append(fcf / ((1.0 + discount_rate) ** yr))

    pv_fcfs = float(sum(pvs))
    terminal_fcf = fcfs[-1] * (1.0 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + discount_rate) ** forecast_years)

    enterprise_value = pv_fcfs + pv_terminal
    equity_value = enterprise_value - (net_debt or 0.0)

    # Apply buyback yield to per-share calc: average shares over forecast horizon
    # (taking midpoint of decay schedule). Positive buyback_yield => fewer future shares.
    if buyback_yield and shares_out:
        # average share count factor over years 1..forecast_years
        decay_factors = [(1.0 - buyback_yield) ** y for y in range(1, forecast_years + 1)]
        avg_decay = sum(decay_factors) / len(decay_factors)
        effective_shares = shares_out * avg_decay
    else:
        effective_shares = shares_out

    fair_value_per_share = equity_value / effective_shares if effective_shares else 0.0

    return {
        "fair_value_per_share": fair_value_per_share,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "pv_of_fcfs": pv_fcfs,
        "pv_of_terminal": pv_terminal,
    }


# ---------------------------------------------------- Reverse DCF -----------
def _reverse_dcf(g: Grounding, base: ScenarioAssumption) -> ReverseDcfResult:
    """
    Solve for the year-1 revenue growth rate that makes FV == current price,
    holding all OTHER Base assumptions constant. Bisection over -10% .. +50%.
    """
    target = g.current_price

    def fv_at(growth: float) -> float:
        out = _compute_dcf(
            revenue_ttm=g.revenue_ttm or 0.0,
            revenue_growth=growth,
            operating_margin=base.operating_margin,
            discount_rate=base.discount_rate,
            terminal_growth=base.terminal_growth,
            capex_pct_revenue=base.capex_pct_revenue,
            tax_rate=g.tax_rate or 0.21,
            shares_out=g.shares_out or 0.0,
            net_debt=g.net_debt or 0.0,
            buyback_yield=g.buyback_yield or 0.0,
        )
        return out["fair_value_per_share"]

    lo, hi = -0.10, 0.50
    fv_lo, fv_hi = fv_at(lo), fv_at(hi)
    implied: Optional[float]
    if (fv_lo - target) * (fv_hi - target) > 0:
        # No bracket — current price outside achievable range
        implied = None
    else:
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            fv_mid = fv_at(mid)
            if abs(fv_mid - target) < 0.01:
                lo = hi = mid
                break
            if (fv_lo - target) * (fv_mid - target) <= 0:
                hi, fv_hi = mid, fv_mid
            else:
                lo, fv_lo = mid, fv_mid
        implied = 0.5 * (lo + hi)

    delta = (implied - base.revenue_growth) if implied is not None else None
    if implied is None:
        interpretation = "Current price is outside the bisection range; market is implying extreme growth/decline."
    elif delta is None:
        interpretation = ""
    elif delta > 0.02:
        interpretation = (
            f"Market is pricing in HIGHER growth than your base case "
            f"({implied*100:.1f}% vs {base.revenue_growth*100:.1f}%). "
            f"Either growth must accelerate, or the stock is overvalued."
        )
    elif delta < -0.02:
        interpretation = (
            f"Market is pricing in LOWER growth than your base case "
            f"({implied*100:.1f}% vs {base.revenue_growth*100:.1f}%). "
            f"If your thesis plays out, the stock is undervalued."
        )
    else:
        interpretation = (
            f"Market is pricing growth roughly in line with your base case "
            f"({implied*100:.1f}% vs {base.revenue_growth*100:.1f}%). Fairly valued on this assumption."
        )

    return ReverseDcfResult(
        implied_revenue_growth=implied,
        base_revenue_growth=base.revenue_growth,
        delta_vs_base=delta,
        interpretation=interpretation,
    )


# -------------------------------------------------- Sensitivity matrix ------
def _sensitivity_matrix(g: Grounding, base: ScenarioAssumption) -> SensitivityMatrix:
    """5x5 grid: WACC ±100bp × terminal_growth ±50bp around base."""
    wacc_axis = [base.discount_rate + d for d in (-0.010, -0.005, 0.0, 0.005, 0.010)]
    tg_axis = [base.terminal_growth + d for d in (-0.010, -0.005, 0.0, 0.005, 0.010)]
    grid: list[list[float]] = []
    for w in wacc_axis:
        row: list[float] = []
        for tg in tg_axis:
            tg_eff = min(tg, w - 0.005)  # keep tg < w
            tg_eff = max(0.0, tg_eff)
            out = _compute_dcf(
                revenue_ttm=g.revenue_ttm or 0.0,
                revenue_growth=base.revenue_growth,
                operating_margin=base.operating_margin,
                discount_rate=max(MIN_WACC, min(MAX_WACC, w)),
                terminal_growth=tg_eff,
                capex_pct_revenue=base.capex_pct_revenue,
                tax_rate=g.tax_rate or 0.21,
                shares_out=g.shares_out or 0.0,
                net_debt=g.net_debt or 0.0,
                buyback_yield=g.buyback_yield or 0.0,
            )
            row.append(float(out["fair_value_per_share"]))
        grid.append(row)
    return SensitivityMatrix(
        wacc_axis=[float(x) for x in wacc_axis],
        terminal_growth_axis=[float(x) for x in tg_axis],
        grid=grid,
        base_wacc=base.discount_rate,
        base_terminal_growth=base.terminal_growth,
    )


# ---------------------------------------------------- Monte Carlo -----------
def _sample_dist(rng: np.random.Generator, name: str, dist: dict, n: int,
                 lo_hi: tuple[float, float]) -> np.ndarray:
    shape = dist.get("shape")
    p = dist.get("params", {})
    lo, hi = lo_hi
    if shape == "normal":
        out = rng.normal(loc=p["mean"], scale=max(1e-4, p["sd"]), size=n)
    elif shape == "triangular":
        out = rng.triangular(left=p["low"], mode=p["mode"], right=p["high"], size=n)
    elif shape == "uniform":
        out = rng.uniform(low=p["low"], high=p["high"], size=n)
    else:
        raise ValueError(f"Unknown distribution shape: {shape}")
    return np.clip(out, lo, hi)


def _run_monte_carlo(g: Grounding, distributions: dict, trials: int = MC_TRIALS) -> MonteCarloResult:
    if not g.shares_out or not g.revenue_ttm:
        raise ValueError("Insufficient grounding data for Monte Carlo (need shares_out and revenue_ttm).")

    trials = max(MC_TRIALS_MIN, min(MC_TRIALS_MAX, int(trials)))
    rng = np.random.default_rng(seed=42)
    rg = _sample_dist(rng, "revenue_growth", distributions["revenue_growth"], trials, RANGES["revenue_growth"])
    om = _sample_dist(rng, "operating_margin", distributions["operating_margin"], trials, RANGES["operating_margin"])
    dr = _sample_dist(rng, "discount_rate", distributions["discount_rate"], trials, (MIN_WACC, MAX_WACC))
    tg = _sample_dist(rng, "terminal_growth", distributions["terminal_growth"], trials, RANGES["terminal_growth"])
    cx = _sample_dist(rng, "capex_pct_revenue", distributions["capex_pct_revenue"], trials, RANGES["capex_pct_revenue"])

    # Enforce tg < dr per trial
    tg = np.where(tg >= dr, np.maximum(0.0, dr - 0.01), tg)

    # Vectorized DCF: build year-by-year revenue/FCFF arrays of shape (trials, years)
    n_years = FORECAST_YEARS
    tax_rate = g.tax_rate or 0.21
    bb = g.buyback_yield or 0.0
    rev0 = g.revenue_ttm
    yrs = np.arange(1, n_years + 1)

    # Linear growth fade: g_t = rg + (tg - rg) * (t-1)/(n-1)
    fade = (yrs - 1) / max(1, n_years - 1)  # shape (n_years,)
    growth_path = rg[:, None] + (tg[:, None] - rg[:, None]) * fade[None, :]  # (trials, years)
    growth_factor = np.cumprod(1.0 + growth_path, axis=1)
    revenue_path = rev0 * growth_factor
    ebit_path = revenue_path * om[:, None]
    nopat_path = ebit_path * (1.0 - tax_rate)
    capex_path = revenue_path * cx[:, None]
    fcf_path = nopat_path - capex_path

    discount_factors = (1.0 + dr[:, None]) ** yrs[None, :]  # (trials, years)
    pv_fcfs = (fcf_path / discount_factors).sum(axis=1)

    terminal_fcf = fcf_path[:, -1] * (1.0 + tg)
    # Avoid zero division: tg already < dr
    terminal_value = terminal_fcf / (dr - tg)
    pv_terminal = terminal_value / ((1.0 + dr) ** n_years)

    enterprise_value = pv_fcfs + pv_terminal
    equity_value = enterprise_value - (g.net_debt or 0.0)

    if bb:
        decay_factors = (1.0 - bb) ** yrs
        avg_decay = decay_factors.mean()
        effective_shares = g.shares_out * avg_decay
    else:
        effective_shares = g.shares_out

    fvs = equity_value / effective_shares
    fvs = fvs[np.isfinite(fvs)]
    if fvs.size == 0:
        raise ValueError("Monte Carlo produced no finite fair values.")

    pcts = np.percentile(fvs, [25, 40, 50, 60, 75])
    counts, edges = np.histogram(fvs, bins=HIST_BINS)
    if fvs.size > SAMPLE_DOWNSAMPLE:
        idx = rng.choice(fvs.size, size=SAMPLE_DOWNSAMPLE, replace=False)
        sample = sorted(fvs[idx].tolist())
    else:
        sample = sorted(fvs.tolist())

    return MonteCarloResult(
        trials=int(fvs.size),
        percentiles={
            "p25": float(pcts[0]),
            "p40": float(pcts[1]),
            "p50": float(pcts[2]),
            "p60": float(pcts[3]),
            "p75": float(pcts[4]),
        },
        mean=float(np.mean(fvs)),
        std=float(np.std(fvs)),
        prob_above_current=float(np.mean(fvs > g.current_price)),
        histogram={
            "bin_edges": [float(x) for x in edges.tolist()],
            "counts": [int(x) for x in counts.tolist()],
        },
        sample=[float(x) for x in sample],
    )


# ----------------------------------------------------- Orchestration --------
def get_dcf(ticker: str, refresh: bool = False, trials: int = MC_TRIALS) -> dict:
    key = f"{ticker.upper()}|{trials}"
    now = time.time()
    if not refresh and key in _CACHE:
        ts, payload = _CACHE[key]
        if now - ts < _CACHE_TTL_SEC:
            payload = dict(payload)
            payload["cached"] = True
            return payload

    g = fetch_grounding(ticker)
    if not g.shares_out or not g.revenue_ttm:
        raise ValueError(
            f"Insufficient financial data for {ticker} (shares_out={g.shares_out}, "
            f"revenue_ttm={g.revenue_ttm}). DCF unavailable."
        )

    raw = _call_llm(g)
    raw = _validate_assumptions(raw, wacc_capm=g.wacc_buildup.wacc)

    scenarios: list[ScenarioAssumption] = []
    scenario_values: list[ScenarioResult] = []
    for sc in raw["scenarios"]:
        assumption = ScenarioAssumption(
            label=sc["label"],
            revenue_growth=sc["revenue_growth"],
            operating_margin=sc["operating_margin"],
            wacc_risk_adj_bps=sc["wacc_risk_adj_bps"],
            discount_rate=sc["discount_rate"],
            terminal_growth=sc["terminal_growth"],
            capex_pct_revenue=sc["capex_pct_revenue"],
            rationale=sc["rationale"],
            strongest_driver=sc["strongest_driver"],
            narrative=sc["narrative"],
        )
        scenarios.append(assumption)
        out = _compute_dcf(
            revenue_ttm=g.revenue_ttm,
            revenue_growth=assumption.revenue_growth,
            operating_margin=assumption.operating_margin,
            discount_rate=assumption.discount_rate,
            terminal_growth=assumption.terminal_growth,
            capex_pct_revenue=assumption.capex_pct_revenue,
            tax_rate=g.tax_rate or 0.21,
            shares_out=g.shares_out,
            net_debt=g.net_debt or 0.0,
            buyback_yield=g.buyback_yield or 0.0,
        )
        scenario_values.append(ScenarioResult(
            label=assumption.label,
            fair_value_per_share=out["fair_value_per_share"],
            upside_pct=(out["fair_value_per_share"] / g.current_price - 1.0) if g.current_price else 0.0,
            enterprise_value=out["enterprise_value"],
            equity_value=out["equity_value"],
            pv_of_fcfs=out["pv_of_fcfs"],
            pv_of_terminal=out["pv_of_terminal"],
        ))

    base = next((s for s in scenarios if s.label == "Base"), scenarios[0])
    reverse = _reverse_dcf(g, base)
    sensitivity = _sensitivity_matrix(g, base)
    mc = _run_monte_carlo(g, raw["distributions"], trials=trials)

    # Verdict from LLM, supplemented with margin-of-safety stat
    v_raw = raw["verdict"]
    margin_of_safety = (mc.percentiles["p25"] - g.current_price) / g.current_price if g.current_price else 0.0
    verdict = Verdict(
        recommendation=v_raw["recommendation"],
        suggested_entry_price=float(v_raw["suggested_entry_price"]),
        suggested_exit_price=float(v_raw["suggested_exit_price"]),
        confidence=float(max(0.0, min(1.0, v_raw["confidence"]))),
        key_assumption_to_monitor=v_raw["key_assumption_to_monitor"],
        margin_of_safety_pct=float(margin_of_safety),
    )

    result = DcfResult(
        ticker=g.ticker,
        grounding=g,
        scenarios=scenarios,
        scenario_values=scenario_values,
        monte_carlo=mc,
        distributions=raw["distributions"],
        reverse_dcf=reverse,
        sensitivity=sensitivity,
        verdict=verdict,
        risks=raw.get("risks", []),
        key_drivers=raw.get("key_drivers", []),
        model=AZURE_OPENAI_DEPLOYMENT,
        cached=False,
    )
    payload = asdict(result)
    _CACHE[key] = (now, payload)
    return payload
