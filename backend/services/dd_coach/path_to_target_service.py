"""Path-to-Target service for DD Coach Screen 6.

Answers the non-finance-user question: "For the stock to reach my target,
WHICH thing has to happen — does the company grow into it, does the market
revalue it, or both?" The output is three concrete paths with plain-English
realism labels (easy / plausible / stretch / unrealistic).

Math
----
Let:
  S = spot price
  T = target price (user input)
  R = T/S − 1                  (required total return)
  C = trailing per-share cash basis (earnings if positive & meaningful,
      else FCF; see ``_pick_cash_basis``)
  M = current multiple = S/C
  P_low, P_high = peer-multiple band for the sector

  Path A — growth only (multiple unchanged):
      required EPS/FCF growth  =  R
  Path B — multiple only (no growth):
      required new multiple    =  T/C  =  M * (1 + R)
  Path C — half and half:
      required growth          =  R / 2
      required new multiple    =  M * (1 + R/2)

When C ≤ 0 (no positive cash basis), Paths A and C are not applicable
and we return ``applicable=False`` on those paths. Path B is only
applicable if both spot and target exist.

Realism bands
-------------
Growth bands compare to ``historical_growth_pct`` (3-yr revenue CAGR):
  easy         req <= H
  plausible    req <= 1.5 * H
  stretch      req <= 3   * H
  unrealistic  req >  3   * H

Multiple bands compare to the sector peer high P_high:
  easy         required <= M (current)
  plausible    required <= P_high
  stretch      required <= 1.5 * P_high
  unrealistic  required >  1.5 * P_high

The mixed path takes the *worse* of its two component realisms.

Side effects
------------
Pure function over a yfinance ``Ticker`` (injectable). No FastAPI imports;
errors are domain exceptions.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

import pandas as pd
import yfinance as yf

from services.dd_coach.errors import DDCoachUnavailable, DDEntryInvalid, DDEntryNotFound
from services.dd_coach.peer_multiples import peer_band

logger = logging.getLogger(__name__)

Realism = Literal["easy", "plausible", "stretch", "unrealistic"]
CashBasis = Literal["earnings", "fcf"]

# Tunables (mirrored in the methodology doc; change both together).
_EARNINGS_QUALITY_MIN = 0.02  # EPS must be >= 2% of revenue/share to use earnings
_PLAUSIBLE_GROWTH_MULT = 1.5
_STRETCH_GROWTH_MULT = 3.0
_PLAUSIBLE_MULT_MULT = 1.0   # vs peer high
_STRETCH_MULT_MULT = 1.5


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathResult:
    applicable: bool
    realism: Realism | None
    required_growth_pct: float | None       # decimal (0.20 = 20%)
    required_multiple: float | None
    note: str


@dataclass(frozen=True)
class PathToTarget:
    ticker: str
    spot: float | None
    target: float
    target_return_pct: float | None         # decimal (T/S − 1)

    cash_basis: CashBasis | None            # which per-share cash we used
    cash_per_share: float | None
    current_multiple: float | None          # M = S/C

    historical_growth_pct: float | None     # 3-yr revenue CAGR (decimal)
    peer_label: str
    peer_multiple_low: float
    peer_multiple_high: float

    path_a_growth_only: PathResult
    path_b_multiple_only: PathResult
    path_c_mixed: PathResult

    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Injection seam (matches data_card_service)
# ---------------------------------------------------------------------------


class TickerProvider(Protocol):
    def get(self, ticker: str) -> Any: ...


class _DefaultTickerProvider:
    def get(self, ticker: str) -> Any:
        return yf.Ticker(ticker)


_default_provider: TickerProvider = _DefaultTickerProvider()


# ---------------------------------------------------------------------------
# Helpers
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


def _per_share(total: float | None, shares: float | None) -> float | None:
    if total is None or shares is None or shares <= 0:
        return None
    return total / shares


def _revenue_cagr_3yr(financials: Any) -> float | None:
    """Compute 3-yr revenue CAGR (decimal). Returns None if not enough data."""
    if financials is None or getattr(financials, "empty", True):
        return None
    try:
        # yfinance financials columns are dates, latest first.
        row_candidates = ["Total Revenue"]
        idx = None
        for name in row_candidates:
            if name in financials.index:
                idx = name
                break
        if idx is None:
            return None
        row = financials.loc[idx].dropna()
        if len(row) < 2:
            return None
        # newest first → reverse so oldest is first
        values = [float(x) for x in row.tolist()][::-1]
        start = values[0]
        end = values[-1]
        years = len(values) - 1
        if start <= 0 or years <= 0:
            return None
        return (end / start) ** (1.0 / years) - 1.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("revenue CAGR compute failed: %s", exc)
        return None


def _pick_cash_basis(
    eps_ttm: float | None,
    fcf_per_share: float | None,
    revenue_per_share: float | None,
) -> tuple[CashBasis | None, float | None]:
    """Pick earnings if positive and earnings/revenue > 2%; else FCF; else None.

    The 2% guard prevents using barely-positive EPS as the basis when FCF
    tells a more honest story (or when EPS is essentially zero).
    """
    if (
        eps_ttm is not None
        and eps_ttm > 0
        and revenue_per_share
        and revenue_per_share > 0
        and (eps_ttm / revenue_per_share) >= _EARNINGS_QUALITY_MIN
    ):
        return "earnings", eps_ttm
    if fcf_per_share is not None and fcf_per_share > 0:
        return "fcf", fcf_per_share
    return None, None


def _growth_realism(required: float, historical: float | None) -> Realism:
    """Bucket a required growth rate vs the historical 3-yr CAGR."""
    if historical is None or historical <= 0:
        # No baseline → judge against an absolute "ambitious but seen" 15%.
        baseline = 0.15
    else:
        baseline = historical
    if required <= baseline:
        return "easy"
    if required <= _PLAUSIBLE_GROWTH_MULT * baseline:
        return "plausible"
    if required <= _STRETCH_GROWTH_MULT * baseline:
        return "stretch"
    return "unrealistic"


def _multiple_realism(
    required: float,
    current: float | None,
    peer_high: float,
) -> Realism:
    if current is not None and required <= current:
        return "easy"
    if required <= _PLAUSIBLE_MULT_MULT * peer_high:
        return "plausible"
    if required <= _STRETCH_MULT_MULT * peer_high:
        return "stretch"
    return "unrealistic"


_REALISM_RANK: dict[Realism, int] = {
    "easy": 0,
    "plausible": 1,
    "stretch": 2,
    "unrealistic": 3,
}


def _worse(a: Realism, b: Realism) -> Realism:
    return a if _REALISM_RANK[a] >= _REALISM_RANK[b] else b


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_path_to_target(
    ticker: str,
    target_price: float,
    *,
    provider: TickerProvider | None = None,
) -> PathToTarget:
    """Compute the three paths from spot to ``target_price`` for ``ticker``."""
    if target_price is None or target_price <= 0:
        raise DDEntryInvalid("target_price must be a positive number.")

    sym = ticker.strip().upper()
    p = provider or _default_provider
    try:
        t = p.get(sym)
    except Exception as exc:
        raise DDCoachUnavailable(f"yfinance lookup failed for {sym}: {exc}") from exc

    info: dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance .info failed for %s: %s", sym, exc)

    financials = getattr(t, "financials", None)
    cashflow = getattr(t, "cashflow", None)

    if not info and financials is None:
        raise DDEntryNotFound(f"No yfinance data for ticker {sym}.")

    spot = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    shares = _safe_float(
        info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"),
    )
    sector = info.get("sector")
    band = peer_band(sector)

    # --- per-share cash basis ---
    eps_ttm = _safe_float(info.get("trailingEps"))
    revenue_ttm = _safe_float(info.get("totalRevenue"))
    revenue_per_share = _per_share(revenue_ttm, shares)

    fcf_ttm = _latest_fcf(financials, cashflow)
    fcf_per_share = _per_share(fcf_ttm, shares)

    basis, cash_per_share = _pick_cash_basis(eps_ttm, fcf_per_share, revenue_per_share)

    current_multiple = (
        spot / cash_per_share
        if spot is not None and cash_per_share and cash_per_share > 0
        else None
    )

    historical_growth = _revenue_cagr_3yr(financials)

    notes: list[str] = []
    if basis is None:
        notes.append(
            "No positive per-share cash (earnings or free cash flow), "
            "so growth-only and mixed paths can't be computed. "
            "Only a re-rating (multiple expansion) path is shown.",
        )
    if sector is None:
        notes.append("Sector unknown — using broad-market peer band as fallback.")
    if historical_growth is None:
        notes.append(
            "Not enough revenue history to compute a 3-year growth baseline "
            "— growth realism is judged against a generic 15% reference.",
        )

    target_return = (target_price / spot - 1.0) if spot and spot > 0 else None

    # --- Path A: growth only ---
    if basis is not None and target_return is not None:
        path_a = PathResult(
            applicable=True,
            realism=_growth_realism(target_return, historical_growth),
            required_growth_pct=target_return,
            required_multiple=None,
            note=_growth_note(target_return, historical_growth),
        )
    else:
        path_a = PathResult(
            applicable=False,
            realism=None,
            required_growth_pct=None,
            required_multiple=None,
            note="Needs positive per-share cash and a spot price.",
        )

    # --- Path B: multiple only ---
    if cash_per_share and cash_per_share > 0:
        required_mult_b = target_price / cash_per_share
        path_b = PathResult(
            applicable=True,
            realism=_multiple_realism(required_mult_b, current_multiple, band.high),
            required_growth_pct=None,
            required_multiple=required_mult_b,
            note=_multiple_note(required_mult_b, current_multiple, band),
        )
    else:
        path_b = PathResult(
            applicable=False,
            realism=None,
            required_growth_pct=None,
            required_multiple=None,
            note="Needs positive per-share cash to compute a multiple.",
        )

    # --- Path C: mixed (half-and-half) ---
    if basis is not None and target_return is not None and current_multiple is not None:
        req_growth_c = target_return / 2.0
        req_mult_c = current_multiple * (1.0 + target_return / 2.0)
        r1 = _growth_realism(req_growth_c, historical_growth)
        r2 = _multiple_realism(req_mult_c, current_multiple, band.high)
        path_c = PathResult(
            applicable=True,
            realism=_worse(r1, r2),
            required_growth_pct=req_growth_c,
            required_multiple=req_mult_c,
            note=(
                f"Cash grows {req_growth_c * 100:.0f}% per year "
                f"AND investors revalue to ~{req_mult_c:.0f}×."
            ),
        )
    else:
        path_c = PathResult(
            applicable=False,
            realism=None,
            required_growth_pct=None,
            required_multiple=None,
            note="Needs positive per-share cash, spot, and current multiple.",
        )

    return PathToTarget(
        ticker=sym,
        spot=spot,
        target=float(target_price),
        target_return_pct=target_return,
        cash_basis=basis,
        cash_per_share=cash_per_share,
        current_multiple=current_multiple,
        historical_growth_pct=historical_growth,
        peer_label=band.label,
        peer_multiple_low=band.low,
        peer_multiple_high=band.high,
        path_a_growth_only=path_a,
        path_b_multiple_only=path_b,
        path_c_mixed=path_c,
        notes=notes,
    )


def _growth_note(required: float, historical: float | None) -> str:
    pct = required * 100.0
    if historical is None:
        return f"Cash per share grows {pct:.0f}% per year — no peer/own history baseline."
    hist_pct = historical * 100.0
    return (
        f"Cash per share grows {pct:.0f}% per year "
        f"vs the company's recent {hist_pct:.0f}% revenue growth."
    )


def _multiple_note(required: float, current: float | None, band: Any) -> str:
    cur_txt = f" (today ~{current:.0f}×)" if current is not None else ""
    return (
        f"Investors revalue to ~{required:.0f}× cash{cur_txt}. "
        f"{band.label} typically trade {band.low:.0f}–{band.high:.0f}×."
    )


def _latest_fcf(financials: Any, cashflow: Any) -> float | None:
    """Best-effort TTM FCF: operating cash flow − capex."""
    if cashflow is None or getattr(cashflow, "empty", True):
        return None
    try:
        ocf = None
        capex = None
        for k in ("Total Cash From Operating Activities", "Operating Cash Flow"):
            if k in cashflow.index:
                ocf = _safe_float(cashflow.loc[k].dropna().iloc[0])
                break
        for k in ("Capital Expenditures", "Capital Expenditure"):
            if k in cashflow.index:
                capex = _safe_float(cashflow.loc[k].dropna().iloc[0])
                break
        if ocf is None:
            return None
        if capex is None:
            return ocf
        # capex is typically negative; FCF = OCF + capex
        return ocf + capex
    except Exception as exc:  # noqa: BLE001
        logger.warning("FCF compute failed: %s", exc)
        return None
