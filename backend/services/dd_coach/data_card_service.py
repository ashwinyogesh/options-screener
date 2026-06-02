"""Data Card service for DD Coach.

Builds the read-only fact panel shown at the top of the DD wizard. Pulls from
yfinance and computes V1 derived signals: hard-rail flags and (for unprofitable
companies) a Growth Lens block.

Design rules (copilot-instructions.md):
  - No FastAPI imports — raise typed domain errors instead.
  - Side-effect-injectable: every yfinance call goes through a small
    ``TickerProvider`` indirection so tests can swap in fake frames.
  - Vocabulary-neutral here. Plain-English rendering lives in the frontend or
    in dedicated `to_plain_english()` helpers; this layer owns *numbers*.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import pandas as pd
import yfinance as yf

from services.dd_coach.errors import DDCoachUnavailable, DDEntryNotFound

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YearlyMetric:
    """A single (year, value) datapoint, oldest-first when in a list."""

    year: int
    value: float | None


@dataclass(frozen=True)
class HardRailFlags:
    """V1 has exactly two non-blocking red banners (locked plan)."""

    balance_sheet_red: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GrowthLens:
    """Shown only when latest FCF < 0 (the locked plan)."""

    gross_margin_3yr: list[YearlyMetric]
    cash_runway_years: float | None
    share_dilution_pct_3yr: float | None
    summary: str


@dataclass(frozen=True)
class DataCard:
    """Snapshot of the facts shown on Screen 1 of the wizard."""

    ticker: str
    company_name: str | None
    sector: str | None
    industry: str | None

    spot_price: float | None
    market_cap: float | None

    revenue_3yr: list[YearlyMetric]
    fcf_3yr: list[YearlyMetric]
    revenue_ttm: float | None
    fcf_ttm: float | None
    cash: float | None
    debt: float | None
    net_cash_position: float | None  # cash − debt; positive = net cash

    price_to_sales_ttm: float | None
    price_to_earnings_ttm: float | None

    flags: HardRailFlags
    growth_lens: GrowthLens | None  # populated only when latest FCF < 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Injection seam for tests
# ---------------------------------------------------------------------------


class TickerProvider(Protocol):
    """All yfinance access funnels through this so unit tests can inject fakes."""

    def get(self, ticker: str) -> Any: ...


class _DefaultTickerProvider:
    def get(self, ticker: str) -> Any:
        return yf.Ticker(ticker)


_default_provider: TickerProvider = _DefaultTickerProvider()


# ---------------------------------------------------------------------------
# Helpers (replicate the dcf_service idioms so we stay consistent)
# ---------------------------------------------------------------------------


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _row(frame: Any, candidates: list[str]) -> pd.Series | None:
    """Return the first matching row in a yfinance DataFrame, or None."""
    if frame is None:
        return None
    try:
        if getattr(frame, "empty", True):
            return None
    except Exception:
        return None
    for name in candidates:
        if name in frame.index:
            return frame.loc[name]
    return None


def _yearly_series(
    row: pd.Series | None, max_years: int = 3,
) -> list[YearlyMetric]:
    """Convert a yfinance row (DatetimeIndex columns) to oldest-first YearlyMetric list."""
    if row is None or row.empty:
        return []
    points: list[tuple[int, float | None]] = []
    for col, val in row.items():
        yr = getattr(col, "year", None)
        if yr is None:
            continue
        points.append((int(yr), _safe_float(val)))
    points.sort(key=lambda p: p[0])
    return [YearlyMetric(year=y, value=v) for y, v in points[-max_years:]]


def _latest_value(frame: Any, candidates: list[str]) -> float | None:
    """Pull the most recent (left-most column) value from a balance-sheet style row."""
    row = _row(frame, candidates)
    if row is None or row.empty:
        return None
    try:
        return _safe_float(row.iloc[0])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------


def _compute_fcf_series(financials: Any, cashflow: Any) -> list[YearlyMetric]:
    """FCF = Operating Cash Flow − |Capex|, computed per year and aligned to
    the cashflow statement's annual columns."""
    ocf_row = _row(cashflow, [
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
        "Cash Flow From Continuing Operating Activities",
    ])
    capex_row = _row(cashflow, [
        "Capital Expenditure",
        "Capital Expenditures",
        "Purchase Of PPE",
    ])
    if ocf_row is None:
        return []
    points: list[tuple[int, float | None]] = []
    for col in ocf_row.index:
        yr = getattr(col, "year", None)
        if yr is None:
            continue
        ocf = _safe_float(ocf_row.get(col))
        capex = _safe_float(capex_row.get(col)) if capex_row is not None else None
        if ocf is None:
            points.append((int(yr), None))
            continue
        fcf = ocf - (abs(capex) if capex is not None else 0.0)
        points.append((int(yr), fcf))
    points.sort(key=lambda p: p[0])
    return [YearlyMetric(year=y, value=v) for y, v in points[-3:]]


_REVENUE_NAMES = ["Total Revenue", "Revenue", "Operating Revenue"]
_OCF_NAMES = [
    "Operating Cash Flow",
    "Total Cash From Operating Activities",
    "Cash Flow From Continuing Operating Activities",
]
_CAPEX_NAMES = [
    "Capital Expenditure",
    "Capital Expenditures",
    "Purchase Of PPE",
]


def _sum_last_n_quarters(row: pd.Series | None, n: int = 4) -> float | None:
    """Sum the most recent ``n`` quarterly values. Returns None unless all ``n``
    quarters are present and numeric — partial TTMs would mislead."""
    if row is None or row.empty:
        return None
    points: list[tuple[Any, float]] = []
    for col, val in row.items():
        v = _safe_float(val)
        if v is None:
            continue
        points.append((col, v))
    if len(points) < n:
        return None
    # yfinance quarterly frames are typically newest-first; sort by date desc.
    try:
        points.sort(key=lambda p: p[0], reverse=True)
    except Exception:
        pass
    return float(sum(v for _, v in points[:n]))


def _compute_revenue_ttm(quarterly_financials: Any) -> float | None:
    return _sum_last_n_quarters(_row(quarterly_financials, _REVENUE_NAMES))


def _compute_fcf_ttm(quarterly_cashflow: Any) -> float | None:
    ocf_row = _row(quarterly_cashflow, _OCF_NAMES)
    if ocf_row is None or ocf_row.empty:
        return None
    capex_row = _row(quarterly_cashflow, _CAPEX_NAMES)
    # Take the same 4 most-recent quarter columns from each row so OCF and
    # capex are aligned in time.
    try:
        cols = sorted(ocf_row.index, reverse=True)[:4]
    except Exception:
        cols = list(ocf_row.index)[:4]
    if len(cols) < 4:
        return None
    total = 0.0
    for col in cols:
        ocf = _safe_float(ocf_row.get(col))
        if ocf is None:
            return None
        capex = _safe_float(capex_row.get(col)) if capex_row is not None else None
        total += ocf - (abs(capex) if capex is not None else 0.0)
    return total


def _compute_gross_margin_series(financials: Any) -> list[YearlyMetric]:
    rev_row = _row(financials, ["Total Revenue"])
    gp_row = _row(financials, ["Gross Profit"])
    if rev_row is None or gp_row is None:
        return []
    points: list[tuple[int, float | None]] = []
    for col in rev_row.index:
        yr = getattr(col, "year", None)
        if yr is None:
            continue
        rev = _safe_float(rev_row.get(col))
        gp = _safe_float(gp_row.get(col))
        gm = (gp / rev) if (rev and rev > 0 and gp is not None) else None
        points.append((int(yr), gm))
    points.sort(key=lambda p: p[0])
    return [YearlyMetric(year=y, value=v) for y, v in points[-3:]]


def _compute_share_dilution_pct(financials: Any) -> float | None:
    """Percent change in diluted shares outstanding over the available window
    (typically 3 years). Returns ``None`` when the series is missing or
    contains zeros that would make the ratio undefined.
    """
    row = _row(financials, [
        "Diluted Average Shares",
        "Basic Average Shares",
        "Ordinary Shares Number",
    ])
    if row is None or row.empty:
        return None
    points: list[tuple[int, float]] = []
    for col, val in row.items():
        yr = getattr(col, "year", None)
        v = _safe_float(val)
        if yr is None or v is None or v <= 0:
            continue
        points.append((int(yr), v))
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])
    earliest = points[0][1]
    latest = points[-1][1]
    return (latest - earliest) / earliest


def _compute_hard_rails(
    fcf_3yr: list[YearlyMetric],
    cash: float | None,
    debt: float | None,
    ebitda: float | None,
) -> HardRailFlags:
    """Locked V1 spec: red banner if ANY of:
      - All 3 most recent years FCF < 0
      - Cash runway < 12 months (cash / avg annual burn < 1.0)
      - Debt/EBITDA > 4
    """
    reasons: list[str] = []

    # Rule 1 — 3 straight years of negative FCF
    if len(fcf_3yr) >= 3 and all((p.value is not None and p.value < 0) for p in fcf_3yr):
        reasons.append("Negative free cash flow 3 years running.")

    # Rule 2 — cash runway < 12 months
    annual_burns = [
        abs(p.value) for p in fcf_3yr if p.value is not None and p.value < 0
    ]
    if cash is not None and annual_burns:
        avg_burn = sum(annual_burns) / len(annual_burns)
        if avg_burn > 0 and (cash / avg_burn) < 1.0:
            reasons.append("Less than 12 months of cash runway at current burn.")

    # Rule 3 — Debt/EBITDA > 4
    if debt is not None and ebitda is not None and ebitda > 0:
        if (debt / ebitda) > 4.0:
            reasons.append(f"Debt is {debt / ebitda:.1f}x annual operating profit (high).")

    return HardRailFlags(balance_sheet_red=bool(reasons), reasons=reasons)


def _build_growth_lens(
    gross_margin_3yr: list[YearlyMetric],
    cash: float | None,
    fcf_3yr: list[YearlyMetric],
    dilution_pct: float | None,
) -> GrowthLens:
    # Cash runway
    burns = [abs(p.value) for p in fcf_3yr if p.value is not None and p.value < 0]
    runway: float | None = None
    if cash is not None and burns:
        avg_burn = sum(burns) / len(burns)
        if avg_burn > 0:
            runway = cash / avg_burn

    # Plain-English summary (2 sentences max)
    parts: list[str] = []
    if len(gross_margin_3yr) >= 2:
        first = gross_margin_3yr[0].value
        last = gross_margin_3yr[-1].value
        if first is not None and last is not None:
            if last > first + 0.02:
                parts.append("Gross margins are expanding as the business scales.")
            elif last < first - 0.02:
                parts.append("Gross margins are deteriorating — unit economics are not yet improving.")
            else:
                parts.append("Gross margins are roughly flat.")
    if runway is not None:
        if runway >= 3:
            parts.append(f"At the current burn rate they have ~{runway:.1f} years of cash before raising again.")
        elif runway >= 1:
            parts.append(f"Cash runway is only ~{runway:.1f} years — dilution risk is real.")
        else:
            parts.append("Cash runway is under a year — near-term dilution or financing is almost certain.")
    if dilution_pct is not None and dilution_pct > 0.10:
        parts.append(f"Share count grew {dilution_pct * 100:.0f}% — your slice of the pie shrank by that much.")

    summary = " ".join(parts) if parts else "Growth-stage business — read the 10-K Risk Factors carefully."

    return GrowthLens(
        gross_margin_3yr=gross_margin_3yr,
        cash_runway_years=runway,
        share_dilution_pct_3yr=dilution_pct,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_data_card(
    ticker: str,
    *,
    provider: TickerProvider | None = None,
) -> DataCard:
    """Return the full DD Coach Screen-1 snapshot for ``ticker``.

    Raises:
        DDEntryNotFound: yfinance returned no usable data for the symbol.
        DDCoachUnavailable: yfinance itself failed (network, rate-limit).
    """
    sym = ticker.strip().upper()
    p = provider or _default_provider

    try:
        t = p.get(sym)
    except Exception as exc:  # network / yfinance internals
        raise DDCoachUnavailable(f"yfinance lookup failed for {sym}: {exc}") from exc

    # info: best-effort; tolerate failures
    info: dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance .info failed for %s: %s", sym, exc)

    if not info and not getattr(t, "financials", None):
        raise DDEntryNotFound(f"No yfinance data for ticker {sym}.")

    # Frames (each individually best-effort)
    financials = getattr(t, "financials", None)
    cashflow = getattr(t, "cashflow", None)
    balance_sheet = getattr(t, "balance_sheet", None)
    quarterly_financials = getattr(t, "quarterly_financials", None)
    quarterly_cashflow = getattr(t, "quarterly_cashflow", None)

    revenue_row = _row(financials, ["Total Revenue"])
    revenue_3yr = _yearly_series(revenue_row, max_years=3)

    fcf_3yr = _compute_fcf_series(financials, cashflow)

    revenue_ttm = _compute_revenue_ttm(quarterly_financials)
    fcf_ttm = _compute_fcf_ttm(quarterly_cashflow)

    cash = _latest_value(balance_sheet, [
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash",
    ])
    debt = _latest_value(balance_sheet, [
        "Total Debt",
        "Long Term Debt",
    ])
    net_cash = None
    if cash is not None or debt is not None:
        net_cash = (cash or 0.0) - (debt or 0.0)

    # EBITDA — used for the Debt/EBITDA rail.
    ebitda = _safe_float(info.get("ebitda"))
    if ebitda is None:
        op_row = _row(financials, [
            "Operating Income", "Total Operating Income As Reported",
        ])
        if op_row is not None and not op_row.empty:
            ebitda = _safe_float(op_row.iloc[0])

    flags = _compute_hard_rails(fcf_3yr, cash, debt, ebitda)

    latest_fcf = fcf_3yr[-1].value if fcf_3yr else None
    growth_lens: GrowthLens | None = None
    if latest_fcf is not None and latest_fcf < 0:
        gross_margin_3yr = _compute_gross_margin_series(financials)
        dilution = _compute_share_dilution_pct(financials)
        growth_lens = _build_growth_lens(gross_margin_3yr, cash, fcf_3yr, dilution)

    return DataCard(
        ticker=sym,
        company_name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        spot_price=_safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
        market_cap=_safe_float(info.get("marketCap")),
        revenue_3yr=revenue_3yr,
        fcf_3yr=fcf_3yr,
        revenue_ttm=revenue_ttm,
        fcf_ttm=fcf_ttm,
        cash=cash,
        debt=debt,
        net_cash_position=net_cash,
        price_to_sales_ttm=_safe_float(info.get("priceToSalesTrailing12Months")),
        price_to_earnings_ttm=_safe_float(info.get("trailingPE")),
        flags=flags,
        growth_lens=growth_lens,
    )
