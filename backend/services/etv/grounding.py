"""Grounding-data fetch for ETV.

yfinance is the primary adapter; SEC EDGAR (XBRL companyfacts) is a
best-effort supplement that fills `None` slots on the resulting
:class:`EtvGrounding` from primary-source filings. Anything still
unavailable is left as ``None`` so downstream stages can flag it as an
explicit assumption.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Optional

import yfinance as yf

from services import fundamentals_service

logger = logging.getLogger(__name__)


@dataclass
class EtvGrounding:
    ticker: str
    company_name: str
    sector: Optional[str]
    industry: Optional[str]
    business_summary: Optional[str]
    # Market
    current_price: float
    market_cap: Optional[float]
    enterprise_value: Optional[float]
    shares_out: Optional[float]
    week52_high: Optional[float]
    week52_low: Optional[float]
    avg_volume_10d: Optional[float]
    implied_vol_30d: Optional[float]
    short_pct_float: Optional[float]
    # Multiples
    trailing_pe: Optional[float]
    forward_pe: Optional[float]
    ev_ebitda: Optional[float]
    ev_revenue: Optional[float]
    price_to_fcf: Optional[float]
    price_to_book: Optional[float]
    # Fundamentals (TTM unless noted)
    revenue_ttm: Optional[float]
    revenue_growth_yoy: Optional[float]
    gross_margin: Optional[float]
    ebitda: Optional[float]
    ebitda_margin: Optional[float]
    operating_income: Optional[float]
    operating_margin: Optional[float]
    net_income: Optional[float]
    eps_ttm: Optional[float]
    free_cash_flow: Optional[float]
    total_debt: Optional[float]
    net_debt: Optional[float]
    cash: Optional[float]
    capex: Optional[float]
    roic: Optional[float]
    # Forward / consensus
    forward_revenue: Optional[float]
    forward_eps: Optional[float]
    long_term_growth: Optional[float]
    analyst_count: Optional[int]
    analyst_recommendation: Optional[str]
    analyst_target_mean: Optional[float]
    analyst_target_high: Optional[float]
    analyst_target_low: Optional[float]
    # Behavior
    sma_50: Optional[float]
    sma_200: Optional[float]
    rsi_14: Optional[float]
    as_of: str


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


def _atm_iv(t: yf.Ticker, price: float) -> Optional[float]:
    """Pick the front-month ATM straddle implied vol (avg of put+call)."""
    try:
        exps = t.options
        if not exps:
            return None
        chain = t.option_chain(exps[0])
        calls, puts = chain.calls, chain.puts
        if calls is None or calls.empty:
            return None
        c = calls.iloc[(calls["strike"] - price).abs().argsort()[:1]]
        iv_c = _safe_float(c["impliedVolatility"].iloc[0]) if not c.empty else None
        if puts is not None and not puts.empty:
            p = puts.iloc[(puts["strike"] - price).abs().argsort()[:1]]
            iv_p = _safe_float(p["impliedVolatility"].iloc[0])
        else:
            iv_p = None
        ivs = [x for x in (iv_c, iv_p) if x is not None and 0 < x < 5]
        if not ivs:
            return None
        return sum(ivs) / len(ivs)
    except Exception as exc:
        logger.debug("ATM IV lookup failed: %s", exc)
        return None


def _sma_rsi(t: yf.Ticker) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (SMA50, SMA200, RSI14) from 1y daily history."""
    try:
        h = t.history(period="1y", auto_adjust=False)
        if h is None or h.empty or "Close" not in h.columns:
            return None, None, None
        close = h["Close"].dropna()
        sma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
        sma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
        # Wilder RSI(14)
        rsi: Optional[float] = None
        if len(close) >= 15:
            delta = close.diff().dropna()
            up = delta.clip(lower=0)
            dn = -delta.clip(upper=0)
            ru = up.ewm(alpha=1 / 14, adjust=False).mean()
            rd = dn.ewm(alpha=1 / 14, adjust=False).mean()
            rs = ru / rd.replace(0, float("nan"))
            rsi_series = 100 - 100 / (1 + rs)
            last = rsi_series.dropna()
            if not last.empty:
                rsi = float(last.iloc[-1])
        return sma50, sma200, rsi
    except Exception as exc:
        logger.debug("SMA/RSI failed: %s", exc)
        return None, None, None


def fetch_grounding(ticker: str) -> EtvGrounding:
    t = yf.Ticker(ticker)
    info: dict = {}
    try:
        info = t.info or {}
    except Exception as exc:
        logger.warning("yfinance info failed for %s: %s", ticker, exc)

    price = (
        _safe_float(info.get("currentPrice"))
        or _safe_float(info.get("regularMarketPrice"))
    )
    if price is None:
        h = t.history(period="5d")
        if h is not None and not h.empty:
            price = float(h["Close"].iloc[-1])
    if price is None:
        raise ValueError(f"No price data for ticker '{ticker}'")

    market_cap = _safe_float(info.get("marketCap"))
    shares_out = _safe_float(info.get("sharesOutstanding")) or (
        market_cap / price if market_cap else None
    )
    # Leave debt/cash as None when yfinance omits them so the EDGAR supplement
    # can fill from primary-source XBRL. The previous `or 0.0` defaults made
    # the slots permanently look populated.
    total_debt = _safe_float(info.get("totalDebt"))
    cash = _safe_float(info.get("totalCash"))
    ev = _safe_float(info.get("enterpriseValue"))
    if ev is None and market_cap is not None and total_debt is not None and cash is not None:
        ev = market_cap + total_debt - cash
    net_debt = (
        (total_debt or 0.0) - (cash or 0.0)
        if (total_debt is not None or cash is not None)
        else None
    )

    iv = _atm_iv(t, price)
    sma50, sma200, rsi14 = _sma_rsi(t)

    fcf = _safe_float(info.get("freeCashflow"))
    p_to_fcf = (market_cap / fcf) if (market_cap and fcf and fcf > 0) else None

    grounding = EtvGrounding(
        ticker=ticker.upper(),
        company_name=info.get("longName") or info.get("shortName") or ticker.upper(),
        sector=info.get("sector"),
        industry=info.get("industry"),
        business_summary=info.get("longBusinessSummary"),
        current_price=float(price),
        market_cap=market_cap,
        enterprise_value=ev,
        shares_out=shares_out,
        week52_high=_safe_float(info.get("fiftyTwoWeekHigh")),
        week52_low=_safe_float(info.get("fiftyTwoWeekLow")),
        avg_volume_10d=_safe_float(info.get("averageVolume10days"))
        or _safe_float(info.get("averageDailyVolume10Day")),
        implied_vol_30d=iv,
        short_pct_float=_safe_float(info.get("shortPercentOfFloat")),
        trailing_pe=_safe_float(info.get("trailingPE")),
        forward_pe=_safe_float(info.get("forwardPE")),
        ev_ebitda=_safe_float(info.get("enterpriseToEbitda")),
        ev_revenue=_safe_float(info.get("enterpriseToRevenue")),
        price_to_fcf=p_to_fcf,
        price_to_book=_safe_float(info.get("priceToBook")),
        revenue_ttm=_safe_float(info.get("totalRevenue")),
        revenue_growth_yoy=_safe_float(info.get("revenueGrowth")),
        gross_margin=_safe_float(info.get("grossMargins")),
        ebitda=_safe_float(info.get("ebitda")),
        ebitda_margin=_safe_float(info.get("ebitdaMargins")),
        operating_income=_safe_float(info.get("operatingIncome")),
        operating_margin=_safe_float(info.get("operatingMargins")),
        net_income=_safe_float(info.get("netIncomeToCommon")),
        eps_ttm=_safe_float(info.get("trailingEps")),
        free_cash_flow=fcf,
        total_debt=total_debt,
        net_debt=net_debt,
        cash=cash,
        capex=_safe_float(info.get("capitalExpenditures")),
        roic=_safe_float(info.get("returnOnInvestedCapital")),
        forward_revenue=_safe_float(info.get("forwardRevenue")),
        forward_eps=_safe_float(info.get("forwardEps")),
        long_term_growth=_safe_float(info.get("earningsGrowth")),
        analyst_count=int(info["numberOfAnalystOpinions"])
        if info.get("numberOfAnalystOpinions") is not None
        else None,
        analyst_recommendation=info.get("recommendationKey"),
        analyst_target_mean=_safe_float(info.get("targetMeanPrice")),
        analyst_target_high=_safe_float(info.get("targetHighPrice")),
        analyst_target_low=_safe_float(info.get("targetLowPrice")),
        sma_50=sma50,
        sma_200=sma200,
        rsi_14=rsi14,
        as_of=time.strftime("%Y-%m-%d"),
    )
    return _supplement_from_edgar(grounding)


# Fields the EDGAR supplement is allowed to fill. Kept narrow on purpose:
# yfinance gives ratios (PE, EV/EBITDA) for free and they're spot-priced;
# EDGAR is for raw accounting line items that yfinance often returns None.
_EDGAR_SUPPLEMENT_FIELDS: frozenset[str] = frozenset({
    "revenue_ttm",
    "operating_income",
    "operating_margin",
    "ebitda",
    "ebitda_margin",
    "net_income",
    "free_cash_flow",
    "capex",
    "cash",
    "total_debt",
    "net_debt",
    "shares_out",
    "roic",
})


def _supplement_from_edgar(g: "EtvGrounding") -> "EtvGrounding":
    """Fill `None` slots on `g` with raw TTM line items from SEC companyfacts.

    Best-effort: any failure (no CIK, network, parse error) is logged and
    the original grounding is returned unchanged. Yfinance values always win
    when both sources are present.
    """
    try:
        asof = date.fromisoformat(g.as_of)
    except ValueError:
        asof = date.today()

    try:
        edgar = fundamentals_service.get_raw_fundamentals(g.ticker, asof)
    except Exception as exc:  # noqa: BLE001 — supplement must never break grounding
        logger.warning("EDGAR supplement failed for %s: %s", g.ticker, exc)
        return g

    if not edgar or all(v is None for v in edgar.values()):
        return g

    valid_names = {f.name for f in fields(g)}
    overrides: dict[str, float] = {}
    for key, value in edgar.items():
        if key not in valid_names or key not in _EDGAR_SUPPLEMENT_FIELDS:
            continue
        if value is None:
            continue
        if getattr(g, key) is None:
            overrides[key] = float(value)

    if not overrides:
        return g

    logger.info(
        "EDGAR supplemented %s: filled %s",
        g.ticker,
        sorted(overrides.keys()),
    )
    return replace(g, **overrides)
