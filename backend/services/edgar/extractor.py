"""PIT factor extraction from SEC companyfacts payloads.

All functions are pure: they take a parsed companyfacts dict and an `asof`
date, and return numeric factors that respect filing-date discipline (no row
whose `filed` date is after `asof` is ever consulted).

Factor signs (calibrated against backtest IC; see ADR-0032):
  +  fcf_yield, roic_ttm, ni_margin, op_margin, asset_turnover
  -  ev_ebitda, ev_sales, ps_ttm, nd_ebitda, debt_to_equity
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable

PIT_FACTORS: tuple[str, ...] = (
    "fcf_yield",
    "ev_ebitda",
    "ev_sales",
    "ps_ttm",
    "roic_ttm",
    "nd_ebitda",
    "debt_to_equity",
    "asset_turnover",
    "ni_margin",
    "op_margin",
    "rev_ttm",
    "net_debt",
    "shares_pit",
)


# Tag aliases — preferred order to fall back through. Mirrors scripts/build_pit_panel.py.
_TAGS_FLOW: dict[str, list[str]] = {
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
_TAGS_STOCK: dict[str, list[str]] = {
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def _records_for(facts: dict[str, Any], aliases: Iterable[str]) -> list[dict[str, Any]]:
    """First alias whose us-gaap entry has data wins; returns parsed records.

    Each record gains `filed_dt`, `end_dt`, optional `start_dt` / `span_days`.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in aliases:
        block = gaap.get(tag)
        if not block:
            continue
        units = block.get("units", {})
        for unit in ("USD", "shares"):
            raw_records = units.get(unit) or []
            if not raw_records:
                continue
            parsed = _parse_records(raw_records)
            if parsed:
                return parsed
    return []


def _parse_records(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for r in raw_records:
        if "val" not in r or "filed" not in r or "end" not in r:
            continue
        filed_dt = _to_date(r["filed"])
        end_dt = _to_date(r["end"])
        if filed_dt is None or end_dt is None:
            continue
        rr = dict(r)
        rr["filed_dt"] = filed_dt
        rr["end_dt"] = end_dt
        start_dt = _to_date(r.get("start"))
        if start_dt is not None:
            rr["start_dt"] = start_dt
            rr["span_days"] = (end_dt - start_dt).days
        parsed.append(rr)
    return parsed


def _records_for_largest(
    facts: dict[str, Any],
    aliases: Iterable[str],
    asof: date,
) -> list[dict[str, Any]]:
    """Pick the alias whose TTM value at `asof` is largest (in absolute terms).

    Used for revenue, where issuers commonly file BOTH a consolidated `Revenues`
    line AND a narrower `RevenueFromContractWithCustomer...` line. The
    "first non-empty alias" heuristic mis-selects on issuers like MSFT/NVDA;
    "largest" reliably picks the consolidated line.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    best_records: list[dict[str, Any]] = []
    best_value: float = 0.0
    for tag in aliases:
        block = gaap.get(tag)
        if not block:
            continue
        units = block.get("units", {})
        for unit in ("USD", "shares"):
            raw_records = units.get(unit) or []
            if not raw_records:
                continue
            parsed = _parse_records(raw_records)
            if not parsed:
                continue
            ttm = _value_flow_ttm(parsed, asof)
            if ttm is None:
                continue
            magnitude = abs(ttm)
            if magnitude > best_value:
                best_value = magnitude
                best_records = parsed
    return best_records


def _value_flow_ttm(records: list[dict[str, Any]], asof: date) -> float | None:
    """TTM (trailing-12-month) value as of `asof`.

    Preference order:
      1. Most recent filing whose period spans 350–380 days.
      2. Sum of last 4 contiguous ~90-day spans.
      3. Annualised partial-period fallback (best effort).
    """
    if not records:
        return None
    visible = [r for r in records if r["filed_dt"] <= asof and "span_days" in r]
    if not visible:
        return None

    annuals = [r for r in visible if 350 <= r["span_days"] <= 380]
    if annuals:
        annuals.sort(key=lambda r: r["end_dt"], reverse=True)
        return float(annuals[0]["val"])

    quarters = [r for r in visible if 85 <= r["span_days"] <= 95]
    if len(quarters) >= 4:
        by_end: dict[date, float] = {}
        quarters.sort(key=lambda r: r["filed_dt"])
        for r in quarters:
            by_end[r["end_dt"]] = float(r["val"])
        ends = sorted(by_end.keys(), reverse=True)[:4]
        if len(ends) == 4 and (ends[0] - ends[3]).days <= 400:
            return sum(by_end[e] for e in ends)

    visible.sort(key=lambda r: (r.get("span_days", 0), r["end_dt"]), reverse=True)
    longest = visible[0]
    span = longest.get("span_days", 0)
    if span >= 350:
        return float(longest["val"])
    if span > 0:
        return float(longest["val"]) * (365.0 / span)
    return None


def _value_stock(records: list[dict[str, Any]], asof: date) -> float | None:
    """Latest balance-sheet value filed on or before `asof`."""
    if not records:
        return None
    visible = [r for r in records if r["filed_dt"] <= asof]
    if not visible:
        return None
    visible.sort(key=lambda r: r["end_dt"], reverse=True)
    return float(visible[0]["val"])


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None:
        return None
    try:
        nf, df = float(num), float(den)
    except (TypeError, ValueError):
        return None
    if df == 0 or math.isnan(nf) or math.isnan(df):
        return None
    return nf / df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_pit_factors(
    facts: dict[str, Any],
    asof: date,
    spot_price: float | None = None,
) -> dict[str, float | None]:
    """Compute PIT factor dict for one ticker as of `asof`.

    `spot_price` enables market-cap-based ratios (PS, EV/EBITDA, FCF yield).
    Pass None to skip those — useful when the caller only wants accounting
    metrics (margins, leverage, asset turnover).

    Returns a dict with one entry per `PIT_FACTORS`; missing values are None.
    """
    flow_recs = {k: _records_for(facts, aliases) for k, aliases in _TAGS_FLOW.items()}
    # Revenue: prefer the alias yielding the largest TTM (consolidated total),
    # not just the first non-empty alias — MSFT/NVDA/etc. file multiple revenue
    # tags and the narrow ones look "wrong" by orders of magnitude.
    flow_recs["revenue"] = _records_for_largest(facts, _TAGS_FLOW["revenue"], asof)
    stock_recs = {k: _records_for(facts, aliases) for k, aliases in _TAGS_STOCK.items()}

    rev = _value_flow_ttm(flow_recs["revenue"], asof)
    op_income = _value_flow_ttm(flow_recs["op_income"], asof)
    da = _value_flow_ttm(flow_recs["depr_amort"], asof) or 0.0
    ni = _value_flow_ttm(flow_recs["net_income"], asof)
    op_cf = _value_flow_ttm(flow_recs["op_cf"], asof)
    capex = _value_flow_ttm(flow_recs["capex"], asof) or 0.0  # SEC reports as positive

    fcf = (op_cf - capex) if op_cf is not None else None
    ebitda = (op_income + da) if op_income is not None else None

    lt_debt = _value_stock(stock_recs["lt_debt"], asof) or 0.0
    lt_debt_curr = _value_stock(stock_recs["lt_debt_curr"], asof) or 0.0
    st_debt = _value_stock(stock_recs["st_debt"], asof) or 0.0
    total_debt = lt_debt + lt_debt_curr + st_debt
    cash = _value_stock(stock_recs["cash"], asof) or 0.0
    net_debt = total_debt - cash

    equity = _value_stock(stock_recs["equity"], asof)
    shares = _value_stock(stock_recs["shares"], asof)
    assets = _value_stock(stock_recs["assets"], asof)
    invested_cap = (total_debt + equity) if equity is not None else None

    mcap = (spot_price * shares) if (spot_price and shares) else None
    ev = (mcap + net_debt) if mcap is not None else None

    out: dict[str, float | None] = {
        "rev_ttm": rev,
        "net_debt": net_debt,
        "shares_pit": shares,
        "fcf_yield": _safe_div(fcf, mcap) if mcap else None,
        "ev_ebitda": _safe_div(ev, ebitda) if (ebitda and ebitda > 0) else None,
        "ev_sales": _safe_div(ev, rev) if (rev and rev > 0) else None,
        "ps_ttm": _safe_div(mcap, rev) if (rev and rev > 0) else None,
        "roic_ttm": (
            _safe_div(op_income * 0.79, invested_cap)
            if (op_income is not None and invested_cap and invested_cap > 0)
            else None
        ),
        "nd_ebitda": (
            _safe_div(net_debt, ebitda)
            if (ebitda and ebitda > 0)
            else None
        ),
        "debt_to_equity": _safe_div(total_debt, equity) if (equity and equity > 0) else None,
        "asset_turnover": _safe_div(rev, assets) if (rev and assets and assets > 0) else None,
        "ni_margin": _safe_div(ni, rev) if (rev and rev > 0) else None,
        "op_margin": _safe_div(op_income, rev) if (rev and rev > 0) else None,
    }
    return _apply_plausibility_guards(out)


# Plausibility ranges for derived ratios. Values outside these bands are almost
# always XBRL data-quality issues (wrong units on shares outstanding, mis-tagged
# revenue line items, etc.) — better to surface None than to poison the scorer.
_PLAUSIBLE_BANDS: dict[str, tuple[float, float]] = {
    "ps_ttm": (0.05, 100.0),
    "ev_sales": (0.05, 100.0),
    "ev_ebitda": (1.0, 500.0),
    "fcf_yield": (-1.0, 1.0),       # FCF yield > 100% of mcap is implausible
    "roic_ttm": (-2.0, 2.0),
    "nd_ebitda": (-50.0, 50.0),
    "debt_to_equity": (0.0, 50.0),
    "asset_turnover": (0.0, 10.0),
    "ni_margin": (-5.0, 1.0),
    "op_margin": (-5.0, 1.0),
}


def _apply_plausibility_guards(
    factors: dict[str, float | None],
) -> dict[str, float | None]:
    cleaned = dict(factors)
    for key, (lo, hi) in _PLAUSIBLE_BANDS.items():
        v = cleaned.get(key)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            cleaned[key] = None
            continue
        if math.isnan(fv) or fv < lo or fv > hi:
            cleaned[key] = None
    return cleaned


def latest_filing_lag_days(facts: dict[str, Any], asof: date) -> int | None:
    """Days between the most recent flow filing visible at `asof` and `asof`.

    Useful for staleness checks; large lags (>120d) indicate the issuer is
    overdue for a 10-Q.
    """
    latest: date | None = None
    for aliases in _TAGS_FLOW.values():
        for r in _records_for(facts, aliases):
            if r["filed_dt"] > asof:
                continue
            if latest is None or r["filed_dt"] > latest:
                latest = r["filed_dt"]
    if latest is None:
        return None
    return (asof - latest).days


# Raw TTM line items that callers (e.g. ETV grounding supplement) want
# verbatim — not ratios. Keep the keys aligned with EtvGrounding field names
# so callers can fill `None` slots directly.
RAW_TTM_FIELDS: tuple[str, ...] = (
    "revenue_ttm",
    "operating_income",
    "operating_margin",
    "ebitda",
    "ebitda_margin",
    "net_income",
    "ni_margin",
    "free_cash_flow",
    "capex",
    "cash",
    "total_debt",
    "net_debt",
    "shares_out",
    "roic",
)


def compute_raw_ttm_fundamentals(
    facts: dict[str, Any],
    asof: date,
) -> dict[str, float | None]:
    """Return raw TTM line items + latest balance-sheet stocks.

    Sibling to :func:`compute_pit_factors`; this one exposes the underlying
    accounting values rather than the derived ratios. Used by the ETV
    grounding supplement to fill yfinance gaps from primary-source XBRL.

    Keys mirror :class:`services.etv.grounding.EtvGrounding` field names.
    Missing values are returned as ``None``.

    `capex` is returned as a *positive* number (matches SEC's
    PaymentsToAcquirePropertyPlantAndEquipment convention and yfinance's
    `capitalExpenditures` sign for free-cash-flow downstream math).
    """
    flow_recs = {k: _records_for(facts, aliases) for k, aliases in _TAGS_FLOW.items()}
    flow_recs["revenue"] = _records_for_largest(facts, _TAGS_FLOW["revenue"], asof)
    stock_recs = {k: _records_for(facts, aliases) for k, aliases in _TAGS_STOCK.items()}

    rev = _value_flow_ttm(flow_recs["revenue"], asof)
    op_income = _value_flow_ttm(flow_recs["op_income"], asof)
    da = _value_flow_ttm(flow_recs["depr_amort"], asof) or 0.0
    ni = _value_flow_ttm(flow_recs["net_income"], asof)
    op_cf = _value_flow_ttm(flow_recs["op_cf"], asof)
    capex = _value_flow_ttm(flow_recs["capex"], asof)
    fcf = (op_cf - (capex or 0.0)) if op_cf is not None else None
    ebitda = (op_income + da) if op_income is not None else None

    lt_debt = _value_stock(stock_recs["lt_debt"], asof) or 0.0
    lt_debt_curr = _value_stock(stock_recs["lt_debt_curr"], asof) or 0.0
    st_debt = _value_stock(stock_recs["st_debt"], asof) or 0.0
    total_debt = lt_debt + lt_debt_curr + st_debt
    cash = _value_stock(stock_recs["cash"], asof)
    net_debt = (total_debt - (cash or 0.0)) if total_debt or cash is not None else None

    equity = _value_stock(stock_recs["equity"], asof)
    shares = _value_stock(stock_recs["shares"], asof)
    invested_cap = (total_debt + equity) if equity is not None else None
    roic = (
        (op_income * 0.79) / invested_cap
        if (op_income is not None and invested_cap and invested_cap > 0)
        else None
    )

    return {
        "revenue_ttm": rev,
        "operating_income": op_income,
        "operating_margin": _safe_div(op_income, rev) if (rev and rev > 0) else None,
        "ebitda": ebitda,
        "ebitda_margin": _safe_div(ebitda, rev) if (ebitda is not None and rev and rev > 0) else None,
        "net_income": ni,
        "ni_margin": _safe_div(ni, rev) if (rev and rev > 0) else None,
        "free_cash_flow": fcf,
        "capex": capex,
        "cash": cash,
        "total_debt": total_debt if (lt_debt or lt_debt_curr or st_debt) else None,
        "net_debt": net_debt,
        "shares_out": shares,
        "roic": roic,
    }
