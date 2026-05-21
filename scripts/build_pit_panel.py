"""Build PIT fundamentals from EDGAR cache and augment the DITM panel.

Strategy (PIT-clean):
  - Flow tags (Revenue, OpInc, NetInc, CashFlow): prefer most recent 12-month
    period whose `filed <= scan_date`. Falls back to sum of last 4 quarterly
    (3-month) spans.
  - Stock tags (Assets, Debt, Equity, Shares): most recent quarter-end snapshot
    whose `filed <= scan_date`.

Output: ditm_backtest_pit.csv
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

EDGAR_DIR = ROOT / "data" / "edgar"
PANEL_IN = ROOT / "ditm_backtest_full.csv"
PANEL_OUT = ROOT / "ditm_backtest_pit.csv"
SECTOR_CACHE = ROOT / "data" / "fundamentals_cache.pkl"  # reuse for sector + ETFs

# Tag aliases: list = preferred order to fall back through.
TAGS_FLOW = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "op_income": ["OperatingIncomeLoss"],
    "depr_amort": [
        "DepreciationAndAmortization",
        "DepreciationDepletionAndAmortization",
        "Depreciation",
    ],
    "net_income": ["NetIncomeLoss"],
    "op_cf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
TAGS_STOCK = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "lt_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "lt_debt_curr": ["LongTermDebtCurrent"],
    "st_debt": ["ShortTermBorrowings"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "equity": ["StockholdersEquity"],
    "shares": ["CommonStockSharesOutstanding"],
}


def load_facts(ticker: str) -> dict | None:
    p = EDGAR_DIR / f"{ticker}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def get_records(facts: dict, tag_aliases: list[str]) -> list[dict]:
    """Return raw records for the first alias that has data. Adds parsed dates."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tag_aliases:
        if tag in gaap:
            units = gaap[tag].get("units", {})
            # Prefer USD/shares whichever exists
            for unit in ("USD", "shares"):
                if unit in units and units[unit]:
                    recs = []
                    for r in units[unit]:
                        if "filed" not in r or "val" not in r:
                            continue
                        rr = dict(r)
                        try:
                            rr["filed_dt"] = pd.Timestamp(r["filed"])
                            rr["end_dt"] = pd.Timestamp(r["end"])
                            if "start" in r:
                                rr["start_dt"] = pd.Timestamp(r["start"])
                                rr["span_days"] = (rr["end_dt"] - rr["start_dt"]).days
                        except Exception:
                            continue
                        recs.append(rr)
                    return recs
    return []


def value_flow_ttm(records: list[dict], asof: pd.Timestamp) -> float | None:
    """Best estimate of TTM value as of `asof` using only filings filed by then.

    Preference: most recent record with span 350–380 days. Fall back to sum of
    most recent 4 contiguous ~90-day spans whose end <= asof.
    """
    if not records:
        return None
    visible = [r for r in records if r["filed_dt"] <= asof and "span_days" in r]
    if not visible:
        return None
    # Try annual span
    annuals = [r for r in visible if 350 <= r["span_days"] <= 380]
    if annuals:
        # most recent end_dt
        annuals.sort(key=lambda r: r["end_dt"], reverse=True)
        return float(annuals[0]["val"])
    # Fall back: sum last 4 quarterly (~85-95 days)
    quarters = [r for r in visible if 85 <= r["span_days"] <= 95]
    if len(quarters) >= 4:
        # dedupe by end_dt (take last filed for each end)
        by_end: dict = {}
        quarters.sort(key=lambda r: r["filed_dt"])
        for r in quarters:
            by_end[r["end_dt"]] = r["val"]
        ends = sorted(by_end.keys(), reverse=True)[:4]
        if len(ends) == 4:
            # ensure they're roughly contiguous (within 400 days)
            if (ends[0] - ends[3]).days <= 400:
                return float(sum(by_end[e] for e in ends))
    # Last resort: longest span available
    visible_with_span = [r for r in visible if r.get("span_days")]
    if visible_with_span:
        visible_with_span.sort(key=lambda r: (r["span_days"], r["end_dt"]), reverse=True)
        # scale to TTM if 6-month or 9-month
        r = visible_with_span[0]
        if r["span_days"] >= 350:
            return float(r["val"])
        return float(r["val"]) * (365.0 / r["span_days"])
    return None


def value_stock(records: list[dict], asof: pd.Timestamp) -> float | None:
    """Latest quarter-end balance value whose filed <= asof."""
    if not records:
        return None
    visible = [r for r in records if r["filed_dt"] <= asof]
    if not visible:
        return None
    visible.sort(key=lambda r: r["end_dt"], reverse=True)
    return float(visible[0]["val"])


def build_ticker_extractor(ticker: str) -> dict | None:
    facts = load_facts(ticker)
    if not facts:
        return None
    out: dict = {"flow": {}, "stock": {}}
    for k, aliases in TAGS_FLOW.items():
        out["flow"][k] = get_records(facts, aliases)
    for k, aliases in TAGS_STOCK.items():
        out["stock"][k] = get_records(facts, aliases)
    return out


def compute_pit(ext: dict, asof: pd.Timestamp) -> dict:
    """Compute factor inputs for one (ticker, asof). Lookahead-clean."""
    flow = {k: value_flow_ttm(v, asof) for k, v in ext["flow"].items()}
    stock = {k: value_stock(v, asof) for k, v in ext["stock"].items()}
    rev = flow.get("revenue")
    opinc = flow.get("op_income")
    da = flow.get("depr_amort") or 0.0
    ni = flow.get("net_income")
    op_cf = flow.get("op_cf")
    capex = flow.get("capex") or 0.0  # SEC reports capex positive
    fcf = (op_cf - capex) if (op_cf is not None) else None
    ebitda = (opinc + da) if (opinc is not None and da is not None) else None
    lt_debt = stock.get("lt_debt") or 0.0
    lt_debt_curr = stock.get("lt_debt_curr") or 0.0
    st_debt = stock.get("st_debt") or 0.0
    total_debt = (lt_debt + lt_debt_curr + st_debt)
    cash = stock.get("cash") or 0.0
    net_debt = total_debt - cash
    equity = stock.get("equity")
    shares = stock.get("shares")
    invested_cap = (total_debt + (equity or 0.0)) if equity is not None else None

    return {
        "rev_ttm": rev,
        "ebitda_ttm": ebitda,
        "ni_ttm": ni,
        "fcf_ttm": fcf,
        "total_debt": total_debt,
        "net_debt": net_debt,
        "equity": equity,
        "invested_cap": invested_cap,
        "shares_pit": shares,
        "opinc_ttm": opinc,
    }


def main() -> None:
    # Allow CLI override: build_pit_panel.py <input.csv> <output.csv>
    panel_in = Path(sys.argv[1]) if len(sys.argv) > 1 else PANEL_IN
    panel_out = Path(sys.argv[2]) if len(sys.argv) > 2 else PANEL_OUT
    print(f"Loading panel: {panel_in}")
    df = pd.read_csv(panel_in)
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    print(f"  rows: {len(df)}")

    # Build a per-ticker extractor once (heavy: parse JSON)
    print("Building per-ticker extractors...")
    extractors: dict[str, dict] = {}
    universe = sorted(df["ticker"].unique())
    n_with_data = 0
    for tkr in universe:
        ext = build_ticker_extractor(tkr)
        if ext:
            extractors[tkr] = ext
            n_with_data += 1
    print(f"  {n_with_data}/{len(universe)} tickers have EDGAR data")

    # Sector ETFs from prior cache (PIT-clean)
    print("Loading sector ETFs from prior cache...")
    with SECTOR_CACHE.open("rb") as fh:
        sc = pickle.load(fh)
    sector_etfs = sc["sector_etfs"]
    sector_etfs.index = pd.to_datetime(sector_etfs.index).tz_localize(None)
    fundamentals_cache = sc["fundamentals"]  # for sector mapping
    SECTOR_ETF = sc.get(
        "sector_etf_map",
        {
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
        },
    )

    def sector_for(t: str) -> str | None:
        rec = fundamentals_cache.get(t)
        return rec.get("sector") if rec else None

    def sector_rs_6m(t: str, asof: pd.Timestamp) -> float | None:
        sec = sector_for(t)
        etf = SECTOR_ETF.get(sec) if sec else None
        if not etf or etf not in sector_etfs.columns:
            return None
        try:
            start = asof - pd.Timedelta(days=183)
            etf_now = sector_etfs[etf].asof(asof)
            etf_then = sector_etfs[etf].asof(start)
            spy_now = sector_etfs["SPY"].asof(asof)
            spy_then = sector_etfs["SPY"].asof(start)
            if any(pd.isna(v) for v in (etf_now, etf_then, spy_now, spy_then)):
                return None
            return (etf_now / etf_then - 1) - (spy_now / spy_then - 1)
        except Exception:
            return None

    print("Computing PIT factors per row...")
    factor_cols = {
        "fcf_yield": [],
        "ev_ebitda": [],
        "ev_sales": [],
        "ps_ttm": [],
        "roic_ttm": [],
        "nd_ebitda": [],
        "debt_to_equity": [],
        "asset_turnover": [],
        "ni_margin": [],
        "op_margin": [],
        "sector_rs_6m": [],
        "sector": [],
        "_pit_lag_days": [],
    }
    for _, row in df.iterrows():
        tkr = row["ticker"]
        sd = row["scan_date"]
        spot = row["spot"]
        ext = extractors.get(tkr)
        if not ext:
            for k in factor_cols:
                factor_cols[k].append(np.nan if k != "sector" else None)
            continue
        pit = compute_pit(ext, sd)
        rev = pit["rev_ttm"]
        ebitda = pit["ebitda_ttm"]
        ni = pit["ni_ttm"]
        fcf = pit["fcf_ttm"]
        nd = pit["net_debt"]
        debt = pit["total_debt"]
        equity = pit["equity"]
        invcap = pit["invested_cap"]
        shares = pit["shares_pit"]
        opinc = pit["opinc_ttm"]

        mcap = (spot * shares) if (spot and shares) else None
        ev = (mcap + nd) if (mcap and nd is not None) else None

        def safe_div(num, den):
            if num is None or den is None:
                return np.nan
            try:
                if den == 0 or pd.isna(num) or pd.isna(den):
                    return np.nan
                return float(num) / float(den)
            except Exception:
                return np.nan

        factor_cols["fcf_yield"].append(safe_div(fcf, mcap))
        factor_cols["ev_ebitda"].append(
            safe_div(ev, ebitda) if (ebitda and ebitda > 0) else np.nan
        )
        factor_cols["ev_sales"].append(safe_div(ev, rev) if (rev and rev > 0) else np.nan)
        factor_cols["ps_ttm"].append(safe_div(mcap, rev) if (rev and rev > 0) else np.nan)
        factor_cols["roic_ttm"].append(
            safe_div(opinc * 0.79, invcap) if (opinc is not None and invcap and invcap > 0) else np.nan
        )
        factor_cols["nd_ebitda"].append(
            safe_div(nd, ebitda) if (ebitda and ebitda > 0 and nd is not None) else np.nan
        )
        factor_cols["debt_to_equity"].append(
            safe_div(debt, equity) if (equity and equity > 0) else np.nan
        )
        pit_assets = value_stock(ext["stock"]["assets"], sd)
        factor_cols["asset_turnover"].append(
            safe_div(rev, pit_assets)
            if (rev and pit_assets and pit_assets > 0)
            else np.nan
        )
        factor_cols["ni_margin"].append(safe_div(ni, rev) if (rev and rev > 0) else np.nan)
        factor_cols["op_margin"].append(safe_div(opinc, rev) if (rev and rev > 0) else np.nan)
        factor_cols["sector_rs_6m"].append(sector_rs_6m(tkr, sd))
        factor_cols["sector"].append(sector_for(tkr))

        # Lag tracking: how stale is the PIT data?
        # Find latest filing date among flow tags <= sd
        latest_filed = None
        for recs in ext["flow"].values():
            for r in recs:
                if r["filed_dt"] <= sd:
                    if latest_filed is None or r["filed_dt"] > latest_filed:
                        latest_filed = r["filed_dt"]
        factor_cols["_pit_lag_days"].append(
            (sd - latest_filed).days if latest_filed is not None else np.nan
        )

    for k, v in factor_cols.items():
        df[k] = v

    df.to_csv(panel_out, index=False)
    print(f"\nSaved -> {panel_out}  rows={len(df)}")
    print("Coverage of new PIT factors:")
    for k in ["fcf_yield", "ev_ebitda", "ev_sales", "ps_ttm", "roic_ttm", "nd_ebitda",
              "debt_to_equity", "asset_turnover", "ni_margin", "op_margin", "sector_rs_6m"]:
        n_ok = df[k].notna().sum()
        print(f"  {k:>16}  {n_ok:>6}/{len(df)}  ({100 * n_ok / len(df):.0f}%)")
    print(f"\nMedian PIT lag (days from latest filing to scan_date): "
          f"{df['_pit_lag_days'].median():.0f}  (p25={df['_pit_lag_days'].quantile(0.25):.0f}, "
          f"p75={df['_pit_lag_days'].quantile(0.75):.0f})")


if __name__ == "__main__":
    main()
