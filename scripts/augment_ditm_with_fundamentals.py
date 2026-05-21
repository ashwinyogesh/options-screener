"""Augment DITM backtest panel with fundamental factors (Option A — current-snapshot proxy).

WARNING: lookahead-contaminated. Uses 2026 quarterly statements to score 2023-2025
trades. Useful as a directional feasibility test only; real PIT IC will be lower.

Factors added per row:
  fcf_yield, ev_ebitda, ps_ttm, op_mgn_trend, roic_ttm, nd_ebitda,
  rev_growth_yoy, ni_growth_yoy, sector_rs_6m

Output: ditm_backtest_augmented.csv
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "fundamentals_cache.pkl"
PANEL_IN = ROOT / "ditm_backtest_full.csv"
PANEL_OUT = ROOT / "ditm_backtest_augmented.csv"

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


def safe_loc(df: pd.DataFrame, row: str, col) -> float | None:
    """Return df.loc[row, col] or None if missing."""
    try:
        if row in df.index and col in df.columns:
            v = df.loc[row, col]
            if pd.isna(v):
                return None
            return float(v)
    except Exception:
        pass
    return None


def first_present(df: pd.DataFrame, candidates: list[str], col) -> float | None:
    """Try multiple row-name aliases."""
    for r in candidates:
        v = safe_loc(df, r, col)
        if v is not None:
            return v
    return None


def sum_quarters(df: pd.DataFrame, candidates: list[str], cols: list) -> float | None:
    """Sum a row across given quarter columns. Returns None if any missing."""
    vals = []
    for c in cols:
        v = first_present(df, candidates, c)
        if v is None:
            return None
        vals.append(v)
    return float(sum(vals))


def compute_ticker_factors(rec: dict) -> dict:
    """Compute one set of TTM/snapshot factors for a ticker (NOT PIT)."""
    inc = rec.get("income_q")
    bs = rec.get("bs_q")
    cf = rec.get("cf_q")
    out: dict = {"sector": rec.get("sector"), "shares_latest": None}

    if inc is None or bs is None or cf is None or inc.empty or bs.empty or cf.empty:
        return out

    # Columns are quarter-end dates, sorted with most recent first by yfinance.
    inc_cols = sorted(inc.columns, reverse=True)
    bs_cols = sorted(bs.columns, reverse=True)
    cf_cols = sorted(cf.columns, reverse=True)

    if len(inc_cols) < 4 or len(cf_cols) < 4:
        return out

    last_4_inc = inc_cols[:4]
    prev_4_inc = inc_cols[4:8] if len(inc_cols) >= 8 else None
    last_4_cf = cf_cols[:4]
    bs_latest = bs_cols[0]

    # TTM aggregates
    rev_ttm = sum_quarters(inc, ["Total Revenue", "Operating Revenue"], last_4_inc)
    opinc_ttm = sum_quarters(inc, ["Operating Income", "Total Operating Income As Reported", "EBIT"], last_4_inc)
    ebitda_ttm = sum_quarters(inc, ["EBITDA", "Normalized EBITDA"], last_4_inc)
    netinc_ttm = sum_quarters(
        inc,
        ["Net Income From Continuing Operation Net Minority Interest", "Net Income", "Net Income Common Stockholders"],
        last_4_inc,
    )
    fcf_ttm = sum_quarters(cf, ["Free Cash Flow"], last_4_cf)

    # Prior TTM (for growth)
    rev_prev = sum_quarters(inc, ["Total Revenue", "Operating Revenue"], prev_4_inc) if prev_4_inc else None
    netinc_prev = (
        sum_quarters(
            inc,
            ["Net Income From Continuing Operation Net Minority Interest", "Net Income"],
            prev_4_inc,
        )
        if prev_4_inc
        else None
    )
    opinc_prev = sum_quarters(inc, ["Operating Income", "EBIT"], prev_4_inc) if prev_4_inc else None

    # Latest balance sheet
    total_debt = first_present(bs, ["Total Debt"], bs_latest) or 0.0
    net_debt = first_present(bs, ["Net Debt"], bs_latest)
    if net_debt is None:
        cash = first_present(bs, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], bs_latest) or 0.0
        net_debt = total_debt - cash
    invested_cap = first_present(bs, ["Invested Capital"], bs_latest)
    equity = first_present(bs, ["Common Stock Equity", "Stockholders Equity"], bs_latest)

    # Latest share count (for market cap calc later — uses historical spot per row)
    shares = rec.get("shares")
    shares_latest = None
    if shares is not None and len(shares) > 0:
        shares_latest = float(shares.iloc[-1])
    if shares_latest is None:
        shares_latest = first_present(bs, ["Ordinary Shares Number", "Share Issued"], bs_latest)
    out["shares_latest"] = shares_latest

    # Operating margin trend = (OpInc/Rev last4) - (OpInc/Rev prev4)
    op_mgn_trend = None
    if opinc_ttm is not None and rev_ttm and rev_ttm != 0 and opinc_prev is not None and rev_prev:
        op_mgn_trend = (opinc_ttm / rev_ttm) - (opinc_prev / rev_prev)

    # ROIC TTM ≈ NOPAT / Invested Capital. Approximate NOPAT = OpInc * (1 - 0.21).
    roic_ttm = None
    if opinc_ttm is not None and invested_cap and invested_cap > 0:
        roic_ttm = (opinc_ttm * 0.79) / invested_cap

    # Net Debt / EBITDA
    nd_ebitda = None
    if ebitda_ttm and ebitda_ttm > 0 and net_debt is not None:
        nd_ebitda = net_debt / ebitda_ttm

    # Growth
    rev_growth = (rev_ttm / rev_prev - 1.0) if (rev_ttm and rev_prev and rev_prev > 0) else None
    ni_growth = None
    if netinc_ttm is not None and netinc_prev not in (None, 0) and netinc_prev > 0:
        ni_growth = netinc_ttm / netinc_prev - 1.0

    out.update(
        {
            "rev_ttm": rev_ttm,
            "opinc_ttm": opinc_ttm,
            "ebitda_ttm": ebitda_ttm,
            "netinc_ttm": netinc_ttm,
            "fcf_ttm": fcf_ttm,
            "total_debt": total_debt,
            "net_debt": net_debt,
            "op_mgn_trend": op_mgn_trend,
            "roic_ttm": roic_ttm,
            "nd_ebitda": nd_ebitda,
            "rev_growth_yoy": rev_growth,
            "ni_growth_yoy": ni_growth,
        }
    )
    return out


def main() -> None:
    print("Loading cache...")
    with CACHE_PATH.open("rb") as fh:
        cache = pickle.load(fh)
    fundamentals = cache["fundamentals"]
    sector_etfs = cache["sector_etfs"]  # daily Close
    sector_etfs.index = pd.to_datetime(sector_etfs.index).tz_localize(None)

    print("Computing per-ticker fundamentals (current snapshot)...")
    per_ticker = {}
    for tkr, rec in fundamentals.items():
        per_ticker[tkr] = compute_ticker_factors(rec)

    n_with_rev = sum(1 for v in per_ticker.values() if v.get("rev_ttm"))
    n_with_fcf = sum(1 for v in per_ticker.values() if v.get("fcf_ttm") is not None)
    n_with_ebitda = sum(1 for v in per_ticker.values() if v.get("ebitda_ttm"))
    print(f"  {n_with_rev}/{len(per_ticker)} have revenue")
    print(f"  {n_with_fcf}/{len(per_ticker)} have FCF")
    print(f"  {n_with_ebitda}/{len(per_ticker)} have EBITDA")

    print(f"Loading panel: {PANEL_IN}")
    df = pd.read_csv(PANEL_IN)
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    print(f"  rows: {len(df)}")

    # Helper: 6m sector RS (PIT-correct)
    def sector_rs_6m(ticker: str, scan_date: pd.Timestamp) -> float | None:
        f = per_ticker.get(ticker)
        if not f:
            return None
        sector = f.get("sector")
        etf = SECTOR_ETF.get(sector) if sector else None
        if not etf or etf not in sector_etfs.columns:
            return None
        sd = scan_date
        start = sd - pd.Timedelta(days=183)
        # ticker history: we don't have it in cache; skip ticker side, use vs SPY for now
        # Better: compute ticker_6m from scan-time spot vs spot 6m prior. We don't have that
        # in the panel directly, so approximate using sector ETF only:
        # Just return the sector ETF's own 6m return as a regime context (limited).
        try:
            etf_series = sector_etfs[etf].dropna()
            etf_now = etf_series.asof(sd)
            etf_then = etf_series.asof(start)
            if pd.isna(etf_now) or pd.isna(etf_then) or etf_then == 0:
                return None
            spy_series = sector_etfs["SPY"].dropna()
            spy_now = spy_series.asof(sd)
            spy_then = spy_series.asof(start)
            if pd.isna(spy_now) or pd.isna(spy_then) or spy_then == 0:
                return None
            return (etf_now / etf_then - 1.0) - (spy_now / spy_then - 1.0)
        except Exception:
            return None

    # Vectorized augment
    print("Augmenting rows...")
    cols_out = {
        "fcf_yield": [],
        "ev_ebitda": [],
        "ps_ttm": [],
        "op_mgn_trend": [],
        "roic_ttm": [],
        "nd_ebitda": [],
        "rev_growth_yoy": [],
        "ni_growth_yoy": [],
        "sector_rs_6m": [],
        "sector": [],
    }
    for _, row in df.iterrows():
        tkr = row["ticker"]
        spot = row["spot"]
        sd = row["scan_date"]
        f = per_ticker.get(tkr, {})
        shares = f.get("shares_latest")
        mcap = (spot * shares) if (shares and spot) else None

        fcf = f.get("fcf_ttm")
        rev = f.get("rev_ttm")
        ebitda = f.get("ebitda_ttm")
        nd = f.get("net_debt")

        cols_out["fcf_yield"].append(fcf / mcap if (fcf is not None and mcap) else np.nan)
        cols_out["ev_ebitda"].append(
            (mcap + nd) / ebitda if (mcap and nd is not None and ebitda and ebitda > 0) else np.nan
        )
        cols_out["ps_ttm"].append(mcap / rev if (mcap and rev and rev > 0) else np.nan)
        cols_out["op_mgn_trend"].append(f.get("op_mgn_trend") if f.get("op_mgn_trend") is not None else np.nan)
        cols_out["roic_ttm"].append(f.get("roic_ttm") if f.get("roic_ttm") is not None else np.nan)
        cols_out["nd_ebitda"].append(f.get("nd_ebitda") if f.get("nd_ebitda") is not None else np.nan)
        cols_out["rev_growth_yoy"].append(
            f.get("rev_growth_yoy") if f.get("rev_growth_yoy") is not None else np.nan
        )
        cols_out["ni_growth_yoy"].append(
            f.get("ni_growth_yoy") if f.get("ni_growth_yoy") is not None else np.nan
        )
        cols_out["sector_rs_6m"].append(sector_rs_6m(tkr, sd))
        cols_out["sector"].append(f.get("sector"))

    for k, v in cols_out.items():
        df[k] = v

    df.to_csv(PANEL_OUT, index=False)
    print(f"Saved -> {PANEL_OUT}  rows={len(df)}")
    print("Coverage of new factors:")
    for k in ["fcf_yield", "ev_ebitda", "ps_ttm", "op_mgn_trend", "roic_ttm", "nd_ebitda", "rev_growth_yoy", "ni_growth_yoy", "sector_rs_6m"]:
        n_ok = df[k].notna().sum()
        print(f"  {k:>16}  {n_ok:>6}/{len(df)}  ({100 * n_ok / len(df):.0f}%)")


if __name__ == "__main__":
    main()
