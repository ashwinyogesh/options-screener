"""Fetch quarterly fundamentals + sector for the universe; cache to disk.

One-time run: pulls quarterly income/balance/cashflow + share count + sector
from yfinance for every ticker in MOMENTUM_UNIVERSE. Caches to a pickle so
the augment step can run fast and offline.

Usage:
    python scripts/fetch_fundamentals.py
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.universe import MOMENTUM_UNIVERSE  # noqa: E402

CACHE_DIR = ROOT / "data"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_PATH = CACHE_DIR / "fundamentals_cache.pkl"

# Sector ETF map for sector-relative-strength factor.
SECTOR_ETF = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
}


def fetch_one(ticker: str) -> dict | None:
    """Pull quarterly statements + sector + share count for one ticker."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        record = {
            "ticker": ticker,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "income_q": tk.quarterly_income_stmt,
            "bs_q": tk.quarterly_balance_sheet,
            "cf_q": tk.quarterly_cashflow,
        }
        # Share count history (for PIT market cap)
        try:
            shares = tk.get_shares_full(start="2022-01-01")
            record["shares"] = shares
        except Exception:
            record["shares"] = None
        return record
    except Exception as exc:
        print(f"  ! {ticker}: {exc}")
        return None


def fetch_sector_etfs(start: str = "2022-01-01") -> pd.DataFrame:
    """Daily close prices for sector ETFs + SPY benchmark."""
    tickers = list(SECTOR_ETF.values()) + ["SPY"]
    df = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    if "Close" in df.columns.get_level_values(0):
        return df["Close"]
    return df


def main() -> None:
    universe = list(MOMENTUM_UNIVERSE)
    print(f"Fetching fundamentals for {len(universe)} tickers...")
    t0 = time.time()
    cache: dict = {"fundamentals": {}, "sector_etf_map": SECTOR_ETF}
    for i, tkr in enumerate(universe, 1):
        rec = fetch_one(tkr)
        if rec is not None:
            cache["fundamentals"][tkr] = rec
        if i % 10 == 0 or i == len(universe):
            print(f"  [{i}/{len(universe)}] elapsed {time.time() - t0:.0f}s")
    print("Fetching sector ETF prices...")
    cache["sector_etfs"] = fetch_sector_etfs()
    with CACHE_PATH.open("wb") as fh:
        pickle.dump(cache, fh)
    print(f"Saved -> {CACHE_PATH}  ({CACHE_PATH.stat().st_size / 1e6:.1f} MB)")
    print(f"Total elapsed: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
