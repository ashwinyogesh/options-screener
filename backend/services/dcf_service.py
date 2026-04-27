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
FORECAST_YEARS_HIGH_GROWTH = 10
HIGH_GROWTH_THRESHOLD = 0.18  # rev_cagr_5y or any-scenario revenue_growth above this -> 10y forecast
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
    "operating_margin_y5": (-0.20, 0.70),
    "mid_growth": (-0.10, 0.50),
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
    gross_margin_ttm: Optional[float]
    operating_margin_3y: list[dict]      # [{year:int, margin:float}]
    historical_metrics: list[dict]       # [{year, revenue_growth, operating_margin, capex_pct_revenue}] up to 5y
    rnd_pct_revenue: Optional[float]
    sbc_ttm: Optional[float]
    sbc_pct_revenue: Optional[float]
    deferred_revenue_yoy: Optional[float]
    roic_ttm: Optional[float]
    tax_rate: Optional[float]
    sector: Optional[str]
    industry: Optional[str]
    buyback_yield: Optional[float]      # NET buyback yield (gross buyback - SBC dilution); used in math
    gross_buyback_yield: Optional[float]
    sbc_dilution_yield: Optional[float]
    share_history: list[dict]            # [{year:int, shares:float}]
    forward_pe: Optional[float]          # market multiple from yfinance info
    market_ev_ebitda: Optional[float]
    market_ev_revenue: Optional[float]
    wacc_buildup: WaccBuildup
    as_of: str


@dataclass
class ScenarioAssumption:
    label: str                          # Conservative | Base | Optimistic
    revenue_growth: float
    operating_margin: float              # Year-1 operating margin
    operating_margin_y5: float           # End-of-explicit-period operating margin
    mid_growth: float                    # Y6-Y10 fade target (used only if 10y forecast)
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
    data_quality_score: float            # 0..1 — how complete is grounding
    deterministic: bool                  # True if entry/exit/recommendation came from formula
    rationale: str                       # one-line explanation of the recommendation


@dataclass
class MultiplesCrossCheck:
    base_fair_value: float
    implied_forward_pe: Optional[float]
    market_forward_pe: Optional[float]
    pe_delta_pct: Optional[float]            # (implied - market) / market
    implied_ev_ebitda: Optional[float]
    market_ev_ebitda: Optional[float]
    ev_ebitda_delta_pct: Optional[float]
    implied_ev_revenue: Optional[float]
    market_ev_revenue: Optional[float]
    ev_revenue_delta_pct: Optional[float]
    diagnostic: str


@dataclass
class FranchiseFlag:
    is_franchise: bool
    roic: Optional[float]
    wacc: float
    spread: Optional[float]              # ROIC - WACC
    terminal_growth_used: float
    message: str


@dataclass
class HorizonSnapshot:
    forecast_years: int
    base_fair_value: float
    base_pv_of_fcfs: float
    base_pv_of_terminal: float
    tv_concentration: float              # pv_of_terminal / enterprise_value
    p25: float
    p50: float
    p75: float
    prob_above_current: float


@dataclass
class HorizonComparison:
    primary_horizon: int                 # 5 or 10 — the auto-picked one used in main fields
    horizon_5y: HorizonSnapshot
    horizon_10y: HorizonSnapshot
    runway_value_pct: float              # (FV_10y - FV_5y) / FV_5y
    tv_concentration_delta: float        # tv_5y - tv_10y (positive = 10y pulls value into explicit period)
    diagnostic: str


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
    multiples: MultiplesCrossCheck
    franchise_flag: FranchiseFlag
    horizon_comparison: HorizonComparison
    forecast_years_used: int
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


def _financials_row(fin, candidates: list[str]):
    """Return first matching row in a yfinance financials/cashflow/balance frame, or None."""
    if fin is None or getattr(fin, "empty", True):
        return None
    for c in candidates:
        if c in fin.index:
            return fin.loc[c].dropna()
    return None


def _compute_sbc(t: yf.Ticker, revenue_ttm: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Returns (sbc_ttm_dollars, sbc_pct_revenue). SBC is the most recent annual figure."""
    try:
        cf = t.cashflow
        row = _financials_row(cf, [
            "Stock Based Compensation",
            "Stock-Based Compensation",
            "Share Based Compensation",
        ])
        if row is None or row.empty:
            return None, None
        sbc = _safe_float(row.iloc[0])
        if sbc is None:
            return None, None
        sbc_abs = abs(sbc)
        pct = (sbc_abs / revenue_ttm) if (revenue_ttm and revenue_ttm > 0) else None
        return float(sbc_abs), pct
    except Exception as exc:
        logger.warning("SBC fetch failed: %s", exc)
        return None, None


def _compute_operating_margin_3y(t: yf.Ticker) -> list[dict]:
    try:
        fin = t.financials
        rev_row = _financials_row(fin, ["Total Revenue"])
        op_row = _financials_row(fin, ["Operating Income", "Total Operating Income As Reported"])
        if rev_row is None or op_row is None:
            return []
        out: list[dict] = []
        for date in rev_row.index[:3]:
            rev = _safe_float(rev_row.get(date))
            op = _safe_float(op_row.get(date))
            yr = getattr(date, "year", None)
            if rev and op is not None and yr:
                out.append({"year": int(yr), "margin": float(op / rev)})
        out.sort(key=lambda r: r["year"])
        return out
    except Exception as exc:
        logger.warning("Op margin 3y fetch failed: %s", exc)
        return []


def _compute_historical_metrics(t: yf.Ticker) -> list[dict]:
    """Per-year history for sanity-checking assumptions: revenue growth (YoY),
    operating margin, and capex/revenue. Returns up to 5 most recent annual rows,
    oldest first. Any individual metric may be None if its source row is missing."""
    try:
        fin = t.financials
        cf = t.cashflow
        rev_row = _financials_row(fin, ["Total Revenue"])
        op_row = _financials_row(fin, ["Operating Income", "Total Operating Income As Reported"])
        capex_row = _financials_row(cf, [
            "Capital Expenditure", "Capital Expenditures", "Purchase Of PPE",
        ])
        if rev_row is None or rev_row.empty:
            return []
        # Build dated revenue map (sorted oldest -> newest) and use it to compute YoY growth.
        dated = []
        for date in rev_row.index:
            yr = getattr(date, "year", None)
            rev = _safe_float(rev_row.get(date))
            if yr and rev and rev > 0:
                dated.append((int(yr), date, rev))
        dated.sort(key=lambda x: x[0])
        out: list[dict] = []
        for i, (yr, date, rev) in enumerate(dated):
            prev_rev = dated[i - 1][2] if i > 0 else None
            growth = ((rev - prev_rev) / prev_rev) if (prev_rev and prev_rev > 0) else None
            op = _safe_float(op_row.get(date)) if op_row is not None else None
            margin = (op / rev) if (op is not None) else None
            capex = _safe_float(capex_row.get(date)) if capex_row is not None else None
            capex_pct = (abs(capex) / rev) if (capex is not None) else None
            out.append({
                "year": yr,
                "revenue_growth": float(growth) if growth is not None else None,
                "operating_margin": float(margin) if margin is not None else None,
                "capex_pct_revenue": float(capex_pct) if capex_pct is not None else None,
            })
        return out[-5:]
    except Exception as exc:
        logger.warning("Historical metrics fetch failed: %s", exc)
        return []


def _compute_rnd_pct(t: yf.Ticker, revenue_ttm: Optional[float]) -> Optional[float]:
    if not revenue_ttm or revenue_ttm <= 0:
        return None
    try:
        fin = t.financials
        row = _financials_row(fin, ["Research And Development", "Research Development"])
        if row is None or row.empty:
            return None
        rnd = _safe_float(row.iloc[0])
        if rnd is None:
            return None
        return float(abs(rnd) / revenue_ttm)
    except Exception as exc:
        logger.warning("R&D fetch failed: %s", exc)
        return None


def _compute_gross_margin(t: yf.Ticker, info: dict) -> Optional[float]:
    gm = _safe_float(info.get("grossMargins"))
    if gm is not None:
        return gm
    try:
        fin = t.financials
        rev_row = _financials_row(fin, ["Total Revenue"])
        gp_row = _financials_row(fin, ["Gross Profit"])
        if rev_row is None or gp_row is None or rev_row.empty or gp_row.empty:
            return None
        rev = _safe_float(rev_row.iloc[0])
        gp = _safe_float(gp_row.iloc[0])
        if rev and gp is not None and rev > 0:
            return float(gp / rev)
    except Exception:
        pass
    return None


def _compute_deferred_revenue_yoy(t: yf.Ticker) -> Optional[float]:
    try:
        bs = t.balance_sheet
        row = _financials_row(bs, [
            "Current Deferred Revenue",
            "Deferred Revenue",
        ])
        if row is None or len(row) < 2:
            return None
        latest = _safe_float(row.iloc[0])
        prior = _safe_float(row.iloc[1])
        if latest is None or prior is None or prior <= 0:
            return None
        return float(latest / prior - 1.0)
    except Exception as exc:
        logger.warning("Deferred revenue fetch failed: %s", exc)
        return None


def _compute_roic(
    revenue_ttm: Optional[float],
    op_margin: Optional[float],
    tax_rate: float,
    total_debt: Optional[float],
    cash: Optional[float],
    market_cap: Optional[float],
    book_equity: Optional[float],
) -> Optional[float]:
    """ROIC = NOPAT / Invested Capital. Invested capital approx = book equity + total debt - cash.

    If book equity unavailable, fall back to market_cap (over-states denominator -> conservative ROIC).
    """
    if not revenue_ttm or op_margin is None:
        return None
    nopat = revenue_ttm * op_margin * (1.0 - tax_rate)
    eq = book_equity if (book_equity and book_equity > 0) else market_cap
    if eq is None:
        return None
    invested = eq + (total_debt or 0.0) - (cash or 0.0)
    if invested <= 0:
        return None
    return float(nopat / invested)


def _compute_book_equity(t: yf.Ticker) -> Optional[float]:
    try:
        bs = t.balance_sheet
        row = _financials_row(bs, [
            "Stockholders Equity",
            "Total Stockholder Equity",
            "Common Stock Equity",
        ])
        if row is None or row.empty:
            return None
        return _safe_float(row.iloc[0])
    except Exception:
        return None


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

    # Gross buyback yield from share count history
    gross_bb, share_history = _compute_buyback_yield(t)
    if gross_bb is not None:
        gross_bb = float(max(-0.08, min(0.08, gross_bb)))

    # SBC: pulls from cashflow statement; pct expressed as fraction of revenue
    sbc_dollars, sbc_pct = _compute_sbc(t, revenue_ttm)
    # SBC dilution yield: SBC dollars / market cap (rough share dilution per year)
    sbc_dilution: Optional[float] = None
    if sbc_dollars is not None and market_cap and market_cap > 0:
        sbc_dilution = float(min(0.10, sbc_dollars / market_cap))

    # NET buyback yield = gross buyback - SBC dilution. This is what's used in math.
    if gross_bb is None and sbc_dilution is None:
        net_bb: Optional[float] = None
    else:
        net_bb = float((gross_bb or 0.0) - (sbc_dilution or 0.0))
        net_bb = max(-0.08, min(0.08, net_bb))

    # Other grounding signals
    gross_margin = _compute_gross_margin(t, info)
    op_margin_3y = _compute_operating_margin_3y(t)
    historical = _compute_historical_metrics(t)
    rnd_pct = _compute_rnd_pct(t, revenue_ttm)
    deferred_yoy = _compute_deferred_revenue_yoy(t)
    book_equity = _compute_book_equity(t)
    roic = _compute_roic(
        revenue_ttm=revenue_ttm,
        op_margin=op_margin,
        tax_rate=tax_rate,
        total_debt=total_debt,
        cash=cash,
        market_cap=market_cap,
        book_equity=book_equity,
    )

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
        gross_margin_ttm=gross_margin,
        operating_margin_3y=op_margin_3y,
        historical_metrics=historical,
        rnd_pct_revenue=rnd_pct,
        sbc_ttm=sbc_dollars,
        sbc_pct_revenue=sbc_pct,
        deferred_revenue_yoy=deferred_yoy,
        roic_ttm=roic,
        tax_rate=tax_rate,
        sector=info.get("sector"),
        industry=info.get("industry"),
        buyback_yield=net_bb,
        gross_buyback_yield=gross_bb,
        sbc_dilution_yield=sbc_dilution,
        share_history=share_history,
        forward_pe=_safe_float(info.get("forwardPE")),
        market_ev_ebitda=_safe_float(info.get("enterpriseToEbitda")),
        market_ev_revenue=_safe_float(info.get("enterpriseToRevenue")),
        wacc_buildup=wacc_bu,
        as_of=time.strftime("%Y-%m-%d"),
    )


# ---------------------------------------------------------------- Prompt ----
_SYSTEM_PROMPT = """You are an expert valuation analyst reasoning in the style of Aswath Damodaran.

You receive grounded financial data for a public company. Backend has ALREADY computed:
- Risk-free rate (10y Treasury)
- WACC via CAPM (using yfinance β + D/E weights + ERP=5.0%)
- NET buyback yield (gross buybacks minus SBC-implied dilution)
- ROIC (return on invested capital)
You DO NOT supply WACC. You supply a small `wacc_risk_adj_bps` offset per scenario (–100bp..+150bp) reflecting scenario-specific risk.

Output STRICT JSON matching the supplied schema. No markdown, no prose outside JSON fields.

For EACH scenario you must supply:
  revenue_growth        — Year-1 revenue growth, decimal (e.g. 0.08 = 8%)
  operating_margin      — Year-1 operating margin
  operating_margin_y5   — Year-5 operating margin. Backend interpolates linearly Y1→Y5.
                          For mature names: ~= operating_margin (flat).
                          For investment-phase growth names: meaningfully HIGHER than TTM,
                          because the bull thesis is operating leverage.
  mid_growth            — Y6–Y10 fade target (used ONLY when forecast extends to 10y; backend
                          decides). Must be between terminal_growth and revenue_growth.
                          For mature names you may set mid_growth ≈ (revenue_growth + terminal_growth)/2.
  wacc_risk_adj_bps     — Integer bps offset around CAPM WACC.
  terminal_growth       — Long-run growth, capped at 4.5% (≤ nominal GDP).
  capex_pct_revenue     — 5-year average reinvestment.
  rationale, strongest_driver, narrative.

Range guardrails (decimal):
    revenue_growth:        -0.20 .. 0.60
    operating_margin:      -0.20 .. 0.70
    operating_margin_y5:   -0.20 .. 0.70
    mid_growth:            -0.10 .. 0.50  (must satisfy terminal_growth ≤ mid_growth ≤ revenue_growth)
    terminal_growth:        0.00 .. 0.045
    capex_pct_revenue:      0.00 .. 0.30
    wacc_risk_adj_bps:    -100   .. +150  (integer)

Discipline:
- Conservative < Base < Optimistic for revenue_growth.
- Conservative WACC adjustment > Base > Optimistic.
- Anchor BASE-CASE to historical data: cite 5y revenue CAGR, TTM operating margin, gross margin,
  and beta in your narrative.
- USE the rich grounding signals: gross_margin distinguishes structural margin from temporary
  investment burn; rnd_pct_revenue identifies investment phase; operating_margin_3y shows
  trajectory; deferred_revenue_yoy is a forward-bookings tell for SaaS / subscriptions.
- If TTM op margin is depressed but R&D % is elevated and gross margin is healthy, this is
  investment phase — your operating_margin_y5 should reflect mid-cycle profitability, not TTM.
- If base-case revenue_growth deviates >300bp from 5y CAGR, justify why explicitly.

Monte Carlo distributions you must supply:
- revenue_growth: normal(mean, sd). Mean ≈ Base. sd ≥ ½ × |Optimistic − Conservative|.
- operating_margin: normal(mean, sd) for Year-1 margin. sd ≥ ½ × |Optimistic − Conservative|.
- operating_margin_y5: normal(mean, sd) for Year-5 margin. Same sd discipline.
- discount_rate: triangular(low, mode, high). mode = Base WACC; low/high = Optimistic / Conservative WACC.
- terminal_growth: uniform(low, high). low=Conservative, high=Optimistic.
- capex_pct_revenue: normal(mean, sd).

Verdict (drives trading decisions):
- recommendation: STRONG_BUY | BUY | HOLD | AVOID | STRONG_AVOID
- suggested_entry_price: typically near P25 fair value, with margin-of-safety discount for
  low-confidence names.
- suggested_exit_price: typically near P75 fair value.
- confidence: 0..1. Lower for cyclicals/opaque accounting/sparse grounding.
- key_assumption_to_monitor: the single thesis-killer variable.

Risks: 4–7 items. Key drivers: 3–5 items.
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
        "operating_margin_3y": g.operating_margin_3y,
        "historical_metrics": g.historical_metrics,
        "gross_margin_ttm": g.gross_margin_ttm,
        "rnd_pct_revenue": g.rnd_pct_revenue,
        "sbc_pct_revenue": g.sbc_pct_revenue,
        "deferred_revenue_yoy": g.deferred_revenue_yoy,
        "roic_ttm": g.roic_ttm,
        "tax_rate": g.tax_rate,
        "sector": g.sector,
        "industry": g.industry,
        "net_buyback_yield": g.buyback_yield,
        "gross_buyback_yield": g.gross_buyback_yield,
        "sbc_dilution_yield": g.sbc_dilution_yield,
        "market_forward_pe": g.forward_pe,
        "market_ev_ebitda": g.market_ev_ebitda,
        "market_ev_revenue": g.market_ev_revenue,
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
                        "operating_margin_y5", "mid_growth",
                        "wacc_risk_adj_bps", "terminal_growth", "capex_pct_revenue",
                        "rationale", "strongest_driver", "narrative",
                    ],
                    "properties": {
                        "label": {"type": "string", "enum": ["Conservative", "Base", "Optimistic"]},
                        "revenue_growth": {"type": "number"},
                        "operating_margin": {"type": "number"},
                        "operating_margin_y5": {"type": "number"},
                        "mid_growth": {"type": "number"},
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
                    "revenue_growth", "operating_margin", "operating_margin_y5",
                    "discount_rate", "terminal_growth", "capex_pct_revenue",
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
                    "operating_margin_y5": {
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
        for k in ("revenue_growth", "operating_margin", "operating_margin_y5",
                  "mid_growth", "terminal_growth", "capex_pct_revenue"):
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
        # mid_growth must satisfy terminal_growth <= mid_growth <= revenue_growth
        mg = float(sc.get("mid_growth", (sc["revenue_growth"] + sc["terminal_growth"]) / 2.0))
        mg = max(sc["terminal_growth"], min(sc["revenue_growth"], mg))
        sc["mid_growth"] = float(mg)

    # Distribution clipping
    dists = d.get("distributions", {})
    for var in ("revenue_growth", "operating_margin", "operating_margin_y5", "capex_pct_revenue"):
        p = dists.get(var, {}).get("params", {})
        if "mean" in p:
            clip_key = var if var in RANGES else "operating_margin"
            p["mean"] = _clip(clip_key, float(p["mean"]))
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
def _build_growth_path(
    revenue_growth: float, mid_growth: float, terminal_growth: float, n_years: int
) -> list[float]:
    """Year-by-year revenue growth rates.

    5y: linear fade revenue_growth -> terminal_growth.
    10y: two-stage — Y1→Y5 fade revenue_growth→mid_growth, Y6→Y10 fade mid_growth→terminal_growth.
    """
    if n_years <= 5:
        if n_years == 1:
            return [revenue_growth]
        return [
            revenue_growth + (terminal_growth - revenue_growth) * t / (n_years - 1)
            for t in range(n_years)
        ]
    # Two-stage 10y
    stage1 = [
        revenue_growth + (mid_growth - revenue_growth) * t / 4
        for t in range(5)
    ]
    stage2 = [
        mid_growth + (terminal_growth - mid_growth) * (t + 1) / 5
        for t in range(5)
    ]
    return stage1 + stage2


def _build_margin_path(
    op_margin_y1: float, op_margin_y5: float, n_years: int
) -> list[float]:
    """Linear fade Y1→Y5; flat at op_margin_y5 thereafter (for 10y forecasts)."""
    out: list[float] = []
    for t in range(n_years):
        if t < 5:
            out.append(op_margin_y1 + (op_margin_y5 - op_margin_y1) * t / 4)
        else:
            out.append(op_margin_y5)
    return out


def _compute_dcf(
    revenue_ttm: float,
    revenue_growth: float,
    operating_margin: float,
    operating_margin_y5: float,
    discount_rate: float,
    terminal_growth: float,
    capex_pct_revenue: float,
    tax_rate: float,
    shares_out: float,
    net_debt: float,
    buyback_yield: float = 0.0,
    forecast_years: int = FORECAST_YEARS,
    mid_growth: Optional[float] = None,
) -> dict:
    """
    FCFF DCF with two-stage growth fade and explicit margin trajectory.
    Terminal value via Gordon Growth.
    """
    if discount_rate <= terminal_growth:
        discount_rate = terminal_growth + 0.01

    if mid_growth is None:
        mid_growth = (revenue_growth + terminal_growth) / 2.0

    growth_path = _build_growth_path(revenue_growth, mid_growth, terminal_growth, forecast_years)
    margin_path = _build_margin_path(operating_margin, operating_margin_y5, forecast_years)

    fcfs: list[float] = []
    pvs: list[float] = []
    rev = revenue_ttm
    for yr in range(1, forecast_years + 1):
        g = growth_path[yr - 1]
        m = margin_path[yr - 1]
        rev = rev * (1.0 + g)
        ebit = rev * m
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

    if buyback_yield and shares_out:
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
        "y1_revenue": revenue_ttm * (1.0 + growth_path[0]),
        "y1_ebit": revenue_ttm * (1.0 + growth_path[0]) * margin_path[0],
    }


# ---------------------------------------------------- Reverse DCF -----------
def _reverse_dcf(g: Grounding, base: ScenarioAssumption, forecast_years: int) -> ReverseDcfResult:
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
            operating_margin_y5=base.operating_margin_y5,
            discount_rate=base.discount_rate,
            terminal_growth=base.terminal_growth,
            capex_pct_revenue=base.capex_pct_revenue,
            tax_rate=g.tax_rate or 0.21,
            shares_out=g.shares_out or 0.0,
            net_debt=g.net_debt or 0.0,
            buyback_yield=g.buyback_yield or 0.0,
            forecast_years=forecast_years,
            mid_growth=base.mid_growth,
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
def _sensitivity_matrix(g: Grounding, base: ScenarioAssumption, forecast_years: int) -> SensitivityMatrix:
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
                operating_margin_y5=base.operating_margin_y5,
                discount_rate=max(MIN_WACC, min(MAX_WACC, w)),
                terminal_growth=tg_eff,
                capex_pct_revenue=base.capex_pct_revenue,
                tax_rate=g.tax_rate or 0.21,
                shares_out=g.shares_out or 0.0,
                net_debt=g.net_debt or 0.0,
                buyback_yield=g.buyback_yield or 0.0,
                forecast_years=forecast_years,
                mid_growth=base.mid_growth,
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


def _run_monte_carlo(
    g: Grounding,
    distributions: dict,
    base: ScenarioAssumption,
    forecast_years: int,
    trials: int = MC_TRIALS,
) -> MonteCarloResult:
    if not g.shares_out or not g.revenue_ttm:
        raise ValueError("Insufficient grounding data for Monte Carlo (need shares_out and revenue_ttm).")

    trials = max(MC_TRIALS_MIN, min(MC_TRIALS_MAX, int(trials)))
    rng = np.random.default_rng(seed=42)
    rg = _sample_dist(rng, "revenue_growth", distributions["revenue_growth"], trials, RANGES["revenue_growth"])
    om1 = _sample_dist(rng, "operating_margin", distributions["operating_margin"], trials, RANGES["operating_margin"])
    om5 = _sample_dist(rng, "operating_margin_y5", distributions["operating_margin_y5"], trials, RANGES["operating_margin_y5"])
    dr = _sample_dist(rng, "discount_rate", distributions["discount_rate"], trials, (MIN_WACC, MAX_WACC))
    tg = _sample_dist(rng, "terminal_growth", distributions["terminal_growth"], trials, RANGES["terminal_growth"])
    cx = _sample_dist(rng, "capex_pct_revenue", distributions["capex_pct_revenue"], trials, RANGES["capex_pct_revenue"])

    # Enforce tg < dr per trial
    tg = np.where(tg >= dr, np.maximum(0.0, dr - 0.01), tg)

    n_years = forecast_years
    tax_rate = g.tax_rate or 0.21
    bb = g.buyback_yield or 0.0
    rev0 = g.revenue_ttm
    yrs = np.arange(1, n_years + 1)
    mid_g = base.mid_growth

    # Growth path: 5y linear OR 10y two-stage (Y1->mid_g over Y1-Y5; mid_g->tg over Y6-Y10)
    if n_years <= 5:
        if n_years == 1:
            growth_path = rg[:, None]
        else:
            t_idx = np.arange(n_years)
            fade = t_idx / (n_years - 1)
            growth_path = rg[:, None] + (tg[:, None] - rg[:, None]) * fade[None, :]
    else:
        # Stage 1: Y1..Y5 fade rg -> mid_g (mid_g fixed across trials per Base scenario)
        fade1 = np.arange(5) / 4
        stage1 = rg[:, None] + (mid_g - rg[:, None]) * fade1[None, :]
        # Stage 2: Y6..Y10 fade mid_g -> tg
        fade2 = (np.arange(5) + 1) / 5
        stage2 = mid_g + (tg[:, None] - mid_g) * fade2[None, :]
        growth_path = np.concatenate([stage1, stage2], axis=1)

    # Margin path: linear Y1->Y5 from om1 to om5; flat at om5 thereafter
    margin_path = np.zeros((trials, n_years))
    for t in range(n_years):
        if t < 5:
            margin_path[:, t] = om1 + (om5 - om1) * t / 4
        else:
            margin_path[:, t] = om5

    growth_factor = np.cumprod(1.0 + growth_path, axis=1)
    revenue_path = rev0 * growth_factor
    ebit_path = revenue_path * margin_path
    nopat_path = ebit_path * (1.0 - tax_rate)
    capex_path = revenue_path * cx[:, None]
    fcf_path = nopat_path - capex_path

    discount_factors = (1.0 + dr[:, None]) ** yrs[None, :]
    pv_fcfs = (fcf_path / discount_factors).sum(axis=1)

    terminal_fcf = fcf_path[:, -1] * (1.0 + tg)
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


# --------------------------------------- Multiples cross-check + franchise --
def _compute_multiples(g: Grounding, base_value: ScenarioResult, base: ScenarioAssumption,
                      forecast_years: int) -> MultiplesCrossCheck:
    """Derive implied forward P/E, EV/EBITDA, EV/Revenue from the base scenario, compare to market."""
    # Y1 numbers from base assumptions
    y1_rev = (g.revenue_ttm or 0.0) * (1.0 + base.revenue_growth)
    y1_ebit = y1_rev * base.operating_margin
    tax = g.tax_rate or 0.21
    y1_ni_proxy = y1_ebit * (1.0 - tax)  # ignores interest expense; rough proxy

    # EBITDA approx = EBIT + capex (D&A ~= capex in mature steady state)
    y1_ebitda = y1_ebit + y1_rev * base.capex_pct_revenue

    implied_pe: Optional[float] = None
    if y1_ni_proxy > 0 and g.shares_out:
        eps_y1 = y1_ni_proxy / g.shares_out
        if eps_y1 > 0:
            implied_pe = base_value.fair_value_per_share / eps_y1

    implied_ev_ebitda: Optional[float] = None
    if y1_ebitda > 0:
        implied_ev_ebitda = base_value.enterprise_value / y1_ebitda

    implied_ev_rev: Optional[float] = None
    if y1_rev > 0:
        implied_ev_rev = base_value.enterprise_value / y1_rev

    pe_delta = (implied_pe - g.forward_pe) / g.forward_pe if (implied_pe and g.forward_pe and g.forward_pe > 0) else None
    ebitda_delta = (implied_ev_ebitda - g.market_ev_ebitda) / g.market_ev_ebitda if (
        implied_ev_ebitda and g.market_ev_ebitda and g.market_ev_ebitda > 0
    ) else None
    rev_delta = (implied_ev_rev - g.market_ev_revenue) / g.market_ev_revenue if (
        implied_ev_rev and g.market_ev_revenue and g.market_ev_revenue > 0
    ) else None

    # Diagnostic
    parts: list[str] = []
    if pe_delta is not None:
        if pe_delta < -0.30:
            parts.append(
                f"Your model implies {abs(pe_delta)*100:.0f}% multiple compression vs market "
                f"({implied_pe:.1f}x vs {g.forward_pe:.1f}x). Disagreement is on growth durability, not value."
            )
        elif pe_delta > 0.30:
            parts.append(
                f"Your model implies a richer P/E than the market "
                f"({implied_pe:.1f}x vs {g.forward_pe:.1f}x) \u2014 your assumptions may be too bullish."
            )
        else:
            parts.append(
                f"Implied fwd P/E ({implied_pe:.1f}x) within ~30% of market ({g.forward_pe:.1f}x) \u2014 multiples and DCF rhyme."
            )
    if ebitda_delta is not None and abs(ebitda_delta) > 0.30:
        parts.append(
            f"EV/EBITDA gap: implied {implied_ev_ebitda:.1f}x vs market {g.market_ev_ebitda:.1f}x."
        )
    diagnostic = " ".join(parts) if parts else "Insufficient market multiples to cross-check."

    return MultiplesCrossCheck(
        base_fair_value=base_value.fair_value_per_share,
        implied_forward_pe=implied_pe,
        market_forward_pe=g.forward_pe,
        pe_delta_pct=pe_delta,
        implied_ev_ebitda=implied_ev_ebitda,
        market_ev_ebitda=g.market_ev_ebitda,
        ev_ebitda_delta_pct=ebitda_delta,
        implied_ev_revenue=implied_ev_rev,
        market_ev_revenue=g.market_ev_revenue,
        ev_revenue_delta_pct=rev_delta,
        diagnostic=diagnostic,
    )


def _compute_franchise_flag(g: Grounding, base: ScenarioAssumption) -> FranchiseFlag:
    roic = g.roic_ttm
    wacc = g.wacc_buildup.wacc
    if roic is None:
        return FranchiseFlag(
            is_franchise=False, roic=None, wacc=wacc, spread=None,
            terminal_growth_used=base.terminal_growth,
            message="ROIC not available (insufficient grounding data).",
        )
    spread = roic - wacc
    is_franchise = roic > wacc * 1.5 and base.terminal_growth < 0.03
    if is_franchise:
        msg = (
            f"High-ROIC franchise: ROIC {roic*100:.0f}% vs WACC {wacc*100:.1f}% "
            f"(spread {spread*100:.1f}pp). Base terminal growth of {base.terminal_growth*100:.1f}% "
            f"likely UNDERSTATES franchise value \u2014 flat-perpetuity Gordon growth ignores reinvestment "
            f"at above-WACC returns. Consider checking sensitivity at terminal_growth = 3.5\u20134.5%."
        )
    elif roic > wacc * 1.5:
        msg = (
            f"High-ROIC franchise (ROIC {roic*100:.0f}% vs WACC {wacc*100:.1f}%) but base "
            f"terminal growth of {base.terminal_growth*100:.1f}% already reflects reinvestment value."
        )
    elif roic < wacc:
        msg = (
            f"WARNING: ROIC ({roic*100:.0f}%) below WACC ({wacc*100:.1f}%). Company is destroying "
            f"value on each marginal dollar of capital \u2014 growth assumptions should be conservative."
        )
    else:
        msg = (
            f"ROIC {roic*100:.0f}% vs WACC {wacc*100:.1f}% \u2014 marginal franchise. "
            f"Standard Gordon growth treatment is appropriate."
        )
    return FranchiseFlag(
        is_franchise=is_franchise, roic=roic, wacc=wacc, spread=spread,
        terminal_growth_used=base.terminal_growth, message=msg,
    )


# ----------------------------------------------------- Orchestration --------
def _decide_forecast_years(scenarios_raw: list[dict], rev_cagr_5y: Optional[float]) -> int:
    """Use 10-year forecast for high-growth names; 5-year otherwise."""
    if rev_cagr_5y is not None and rev_cagr_5y > HIGH_GROWTH_THRESHOLD:
        return FORECAST_YEARS_HIGH_GROWTH
    for sc in scenarios_raw:
        if float(sc.get("revenue_growth", 0)) > 0.20:
            return FORECAST_YEARS_HIGH_GROWTH
    return FORECAST_YEARS


def _compute_for_horizon(
    g: Grounding,
    scenarios: list[ScenarioAssumption],
    distributions: dict,
    forecast_years: int,
    trials: int,
) -> tuple[list[ScenarioResult], MonteCarloResult, MultiplesCrossCheck, ScenarioResult]:
    """Run scenario DCFs + MC + multiples for a given forecast horizon. Pure compute, no I/O."""
    scenario_values: list[ScenarioResult] = []
    for assumption in scenarios:
        out = _compute_dcf(
            revenue_ttm=g.revenue_ttm,
            revenue_growth=assumption.revenue_growth,
            operating_margin=assumption.operating_margin,
            operating_margin_y5=assumption.operating_margin_y5,
            discount_rate=assumption.discount_rate,
            terminal_growth=assumption.terminal_growth,
            capex_pct_revenue=assumption.capex_pct_revenue,
            tax_rate=g.tax_rate or 0.21,
            shares_out=g.shares_out,
            net_debt=g.net_debt or 0.0,
            buyback_yield=g.buyback_yield or 0.0,
            forecast_years=forecast_years,
            mid_growth=assumption.mid_growth,
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
    base_value = next((sv for sv in scenario_values if sv.label == base.label), scenario_values[0])
    mc = _run_monte_carlo(g, distributions, base=base, forecast_years=forecast_years, trials=trials)
    multiples = _compute_multiples(g, base_value, base, forecast_years)
    return scenario_values, mc, multiples, base_value


def _build_horizon_snapshot(forecast_years: int, base_value: ScenarioResult, mc: MonteCarloResult) -> HorizonSnapshot:
    ev = base_value.enterprise_value or 1.0
    return HorizonSnapshot(
        forecast_years=forecast_years,
        base_fair_value=base_value.fair_value_per_share,
        base_pv_of_fcfs=base_value.pv_of_fcfs,
        base_pv_of_terminal=base_value.pv_of_terminal,
        tv_concentration=float(base_value.pv_of_terminal / ev) if ev else 0.0,
        p25=mc.percentiles["p25"],
        p50=mc.percentiles["p50"],
        p75=mc.percentiles["p75"],
        prob_above_current=mc.prob_above_current,
    )


def _build_horizon_comparison(primary: int, snap5: HorizonSnapshot, snap10: HorizonSnapshot) -> HorizonComparison:
    fv5 = snap5.base_fair_value
    fv10 = snap10.base_fair_value
    runway = (fv10 - fv5) / fv5 if fv5 > 0 else 0.0
    tv_delta = snap5.tv_concentration - snap10.tv_concentration
    if abs(runway) < 0.05:
        diag = (
            "Mature business profile \u2014 5y and 10y models agree within 5%. "
            "Horizon choice is not material; perpetuity assumption is doing most of the work."
        )
    elif runway > 0.20:
        diag = (
            f"Significant runway value: 10y FV is {runway*100:.0f}% above 5y FV. "
            f"5y model is structurally truncating the explicit-growth period; "
            f"a 10y read better captures the bull thesis."
        )
    elif runway > 0.05:
        diag = (
            f"Modest runway premium: 10y FV is {runway*100:.0f}% above 5y. "
            f"Some growth left in the explicit period beyond Y5."
        )
    elif runway < -0.10:
        diag = (
            f"Negative runway: 10y FV is {abs(runway)*100:.0f}% BELOW 5y. "
            f"Implies margin or growth fade in Y6-Y10 \u2014 cyclical signature or peak-margin exposure."
        )
    else:
        diag = (
            f"10y FV is {runway*100:.0f}% vs 5y \u2014 mild fade in extended period."
        )
    return HorizonComparison(
        primary_horizon=primary,
        horizon_5y=snap5,
        horizon_10y=snap10,
        runway_value_pct=float(runway),
        tv_concentration_delta=float(tv_delta),
        diagnostic=diag,
    )


def _compute_data_quality_score(g: Grounding) -> float:
    """0..1 score \u2014 how complete is the grounding for trustworthy verdict?"""
    checks = [
        g.beta is not None and abs((g.beta or 1.0) - 1.0) > 1e-9,  # real beta, not default 1.0
        len(g.revenue_history) >= 4,
        g.tax_rate is not None,
        g.buyback_yield is not None,
        g.roic_ttm is not None,
        g.market_cap is not None and g.market_cap > 0,
        g.gross_margin_ttm is not None,
        g.operating_margin_ttm is not None,
    ]
    return sum(1.0 for c in checks if c) / len(checks)


def _compute_verdict_deterministic(
    g: Grounding,
    mc: MonteCarloResult,
    base_value: ScenarioResult,
    multiples: MultiplesCrossCheck,
    key_assumption_to_monitor: str,
) -> Verdict:
    """Deterministic verdict from MC distribution + data quality. Same inputs \u2192 same output."""
    price = g.current_price or 0.0
    p25 = mc.percentiles["p25"]
    p50 = mc.percentiles["p50"]
    p75 = mc.percentiles["p75"]

    # Margin-of-safety discount on entry depends on data quality + multiples disagreement.
    dq = _compute_data_quality_score(g)
    # Larger MoS for low-confidence names; baseline 5% MoS, up to 15% if quality low.
    mos = 0.05 + (1.0 - dq) * 0.10

    entry = float(p25 * (1.0 - mos))
    exit_ = float(p75)

    # Confidence: blends spread tightness with data quality.
    spread_tightness = max(0.0, 1.0 - ((p75 - p25) / p50)) if p50 > 0 else 0.0
    confidence = max(0.0, min(1.0, 0.5 * spread_tightness + 0.5 * dq))

    # Decision tree.
    if price <= entry and confidence >= 0.6:
        rec = "STRONG_BUY"
    elif price <= entry:
        rec = "BUY"
    elif price <= p50:
        rec = "BUY" if confidence >= 0.55 else "HOLD"
    elif price <= exit_:
        rec = "HOLD"
    elif price <= exit_ * 1.10:
        rec = "AVOID"
    else:
        rec = "STRONG_AVOID"

    # Multiples disagreement amplifies AVOID-side calls.
    if rec in ("HOLD", "AVOID") and multiples.pe_delta_pct is not None and multiples.pe_delta_pct < -0.30:
        rec = "AVOID" if rec == "HOLD" else "STRONG_AVOID"

    margin_of_safety = (p25 - price) / price if price else 0.0

    rationale_parts: list[str] = []
    if price <= entry:
        rationale_parts.append(f"price {price:.2f} \u2264 P25\u00d7(1-MoS) entry {entry:.2f}")
    elif price <= p50:
        rationale_parts.append(f"price {price:.2f} between entry and P50 ({p50:.2f})")
    elif price <= exit_:
        rationale_parts.append(f"price {price:.2f} between P50 and P75 exit ({exit_:.2f})")
    else:
        rationale_parts.append(f"price {price:.2f} above P75 exit ({exit_:.2f})")
    rationale_parts.append(f"confidence {confidence*100:.0f}% (data quality {dq*100:.0f}%)")
    rationale = " \u00b7 ".join(rationale_parts)

    return Verdict(
        recommendation=rec,  # type: ignore[arg-type]
        suggested_entry_price=entry,
        suggested_exit_price=exit_,
        confidence=float(confidence),
        key_assumption_to_monitor=key_assumption_to_monitor,
        margin_of_safety_pct=float(margin_of_safety),
        data_quality_score=float(dq),
        deterministic=True,
        rationale=rationale,
    )


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

    primary_horizon = _decide_forecast_years(raw.get("scenarios", []), g.revenue_cagr_5y)
    logger.info("DCF for %s primary horizon = %d years (rev_cagr_5y=%s)",
                g.ticker, primary_horizon, g.revenue_cagr_5y)

    scenarios: list[ScenarioAssumption] = [
        ScenarioAssumption(
            label=sc["label"],
            revenue_growth=sc["revenue_growth"],
            operating_margin=sc["operating_margin"],
            operating_margin_y5=sc["operating_margin_y5"],
            mid_growth=sc["mid_growth"],
            wacc_risk_adj_bps=sc["wacc_risk_adj_bps"],
            discount_rate=sc["discount_rate"],
            terminal_growth=sc["terminal_growth"],
            capex_pct_revenue=sc["capex_pct_revenue"],
            rationale=sc["rationale"],
            strongest_driver=sc["strongest_driver"],
            narrative=sc["narrative"],
        )
        for sc in raw["scenarios"]
    ]

    # Compute BOTH horizons; primary becomes the main payload.
    sv5, mc5, mult5, bv5 = _compute_for_horizon(g, scenarios, raw["distributions"], FORECAST_YEARS, trials)
    sv10, mc10, mult10, bv10 = _compute_for_horizon(g, scenarios, raw["distributions"], FORECAST_YEARS_HIGH_GROWTH, trials)

    if primary_horizon == FORECAST_YEARS_HIGH_GROWTH:
        scenario_values, mc, multiples, base_value = sv10, mc10, mult10, bv10
    else:
        scenario_values, mc, multiples, base_value = sv5, mc5, mult5, bv5

    base = next((s for s in scenarios if s.label == "Base"), scenarios[0])
    reverse = _reverse_dcf(g, base, forecast_years=primary_horizon)
    sensitivity = _sensitivity_matrix(g, base, forecast_years=primary_horizon)
    franchise = _compute_franchise_flag(g, base)

    snap5 = _build_horizon_snapshot(FORECAST_YEARS, bv5, mc5)
    snap10 = _build_horizon_snapshot(FORECAST_YEARS_HIGH_GROWTH, bv10, mc10)
    horizon_comparison = _build_horizon_comparison(primary_horizon, snap5, snap10)

    # Deterministic verdict; LLM still supplies key_assumption_to_monitor.
    v_raw = raw.get("verdict", {})
    key_assumption = v_raw.get("key_assumption_to_monitor", "(not provided)")
    verdict = _compute_verdict_deterministic(g, mc, base_value, multiples, key_assumption)

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
        multiples=multiples,
        franchise_flag=franchise,
        horizon_comparison=horizon_comparison,
        forecast_years_used=primary_horizon,
        risks=raw.get("risks", []),
        key_drivers=raw.get("key_drivers", []),
        model=AZURE_OPENAI_DEPLOYMENT,
        cached=False,
    )
    payload = asdict(result)
    _CACHE[key] = (now, payload)
    return payload
