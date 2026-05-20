"""
Fetches OHLC price history, the risk-free rate, and news headlines from Yahoo Finance via yfinance.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_FALLBACK_RISK_FREE_RATE = 0.045  # 4.5% annual
_RF_CACHE_TTL = 3600.0  # seconds — rate changes slowly; 1 h is more than adequate
_rf_cache: tuple[float, float] | None = None  # (rate, monotonic_time)


def get_ohlc(symbol: str, period: str = "1y") -> pd.DataFrame:
    """
    Returns a DataFrame with daily OHLC columns: Open, High, Low, Close, Volume.
    Index is a DatetimeIndex (UTC-aware).
    Raises ValueError if no data is returned.
    """
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"No OHLC data returned for symbol '{symbol}'")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(subset=["Close"], inplace=True)
    return df


def get_risk_free_rate() -> float:
    """
    Fetches the 13-week Treasury bill yield (^IRX) as a decimal annual rate.
    Result is cached for 1 hour — the rate changes slowly and this function
    is called on every screener request. Falls back to FALLBACK_RISK_FREE_RATE
    on any error; failures are not cached so the next request retries.
    """
    global _rf_cache
    now = time.monotonic()
    if _rf_cache is not None and now - _rf_cache[1] < _RF_CACHE_TTL:
        return _rf_cache[0]
    try:
        irx = yf.Ticker("^IRX")
        hist = irx.history(period="5d")
        if hist is not None and not hist.empty:
            rate = float(hist["Close"].iloc[-1]) / 100.0
            if 0 < rate < 1:
                _rf_cache = (rate, now)
                return rate
    except Exception as exc:
        logger.warning("Could not fetch risk-free rate: %s — using fallback %.3f", exc, _FALLBACK_RISK_FREE_RATE)
    return _FALLBACK_RISK_FREE_RATE


def get_ticker_info(symbol: str) -> dict:
    """
    Returns company profile + current market context for AI insight enrichment.

    Fields returned:
      sector, industry, business_summary (≤300 chars),
      52w_high, 52w_low,
      vix_current (float | None), vix_regime ("Calm" | "Normal" | "Elevated" | "Panic" | "Unknown")

    Never raises — all fields default to None / "Unknown" on failure so the
    insight service can still run with whatever data is available.
    """
    profile: dict = {
        "sector": None,
        "industry": None,
        "business_summary": None,
        "52w_high": None,
        "52w_low": None,
        "trailing_pe": None,
        "forward_pe": None,
        "revenue_growth_pct": None,
        "free_cashflow_b": None,
        "debt_to_equity": None,
        "return_on_equity_pct": None,
        "vix_current": None,
        "vix_regime": "Unknown",
    }
    # --- ticker profile ---
    try:
        info = yf.Ticker(symbol).info or {}
        profile["sector"] = info.get("sector")
        profile["industry"] = info.get("industry")
        summary: str = info.get("longBusinessSummary", "") or ""
        profile["business_summary"] = summary[:300] if summary else None
        profile["52w_high"] = info.get("fiftyTwoWeekHigh")
        profile["52w_low"] = info.get("fiftyTwoWeekLow")
        # fundamental fields for AI ownership gate
        try:
            raw_pe = info.get("trailingPE")
            profile["trailing_pe"] = round(float(raw_pe), 1) if raw_pe is not None else None
        except (TypeError, ValueError):
            pass
        try:
            raw_fpe = info.get("forwardPE")
            profile["forward_pe"] = round(float(raw_fpe), 1) if raw_fpe is not None else None
        except (TypeError, ValueError):
            pass
        try:
            raw_rev = info.get("revenueGrowth")
            profile["revenue_growth_pct"] = round(float(raw_rev) * 100, 1) if raw_rev is not None else None
        except (TypeError, ValueError):
            pass
        try:
            raw_fcf = info.get("freeCashflow")
            profile["free_cashflow_b"] = round(float(raw_fcf) / 1e9, 2) if raw_fcf is not None else None
        except (TypeError, ValueError):
            pass
        try:
            raw_de = info.get("debtToEquity")
            profile["debt_to_equity"] = round(float(raw_de), 2) if raw_de is not None else None
        except (TypeError, ValueError):
            pass
        try:
            raw_roe = info.get("returnOnEquity")
            profile["return_on_equity_pct"] = round(float(raw_roe) * 100, 1) if raw_roe is not None else None
        except (TypeError, ValueError):
            pass
    except Exception as exc:
        logger.warning("Ticker info fetch failed for %s: %s", symbol, exc)

    # --- VIX ---
    try:
        vix_df = yf.Ticker("^VIX").history(period="5d", auto_adjust=True)
        if vix_df is not None and not vix_df.empty:
            vix_val = float(vix_df["Close"].iloc[-1])
            profile["vix_current"] = round(vix_val, 2)
            if vix_val < 15:
                profile["vix_regime"] = "Calm"
            elif vix_val < 25:
                profile["vix_regime"] = "Normal"
            elif vix_val < 35:
                profile["vix_regime"] = "Elevated"
            else:
                profile["vix_regime"] = "Panic"
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)

    return profile


def get_news(symbol: str, max_age_hours: int = 72, max_items: int = 8) -> list[dict]:
    """
    Returns recent news headlines for a symbol from Yahoo Finance.

    Each item is a dict with keys: title, summary, published (ISO string).
    Items older than max_age_hours are excluded. Returns at most max_items.
    Returns an empty list on any failure — callers must tolerate missing news.
    """
    try:
        ticker = yf.Ticker(symbol)
        raw: list = ticker.news or []
        cutoff = datetime.now(tz=timezone.utc).timestamp() - max_age_hours * 3600
        items: list[dict] = []
        for entry in raw:
            content = entry.get("content", {})
            pub_str: str = content.get("pubDate", "")
            try:
                pub_ts = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).timestamp()
            except (ValueError, AttributeError):
                pub_ts = cutoff  # include if unparseable
            if pub_ts < cutoff:
                continue
            title: str = content.get("title", "").strip()
            summary: str = content.get("summary", "").strip()
            if not title:
                continue
            items.append({"title": title, "summary": summary, "published": pub_str})
            if len(items) >= max_items:
                break
        return items
    except Exception as exc:
        logger.warning("News fetch failed for %s: %s", symbol, exc)
        return []
