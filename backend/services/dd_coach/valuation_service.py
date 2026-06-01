"""Valuation service for DD Coach.

Implements Q5: bear / base / bull per-share fair-value ranges using exactly
three methods (locked V1 spec). The method is auto-selected from data card
signals so the user is never asked to pick a financial framework.

Methods
-------
1. **Multiple-Based** — for profitable, 3-yr-positive-FCF companies (MSFT, AAPL).
   Formula:  per_share_value = (forward_eps × target_pe)
   Bear/base/bull come from a (low, mid, high) target_pe triple (defaults
   from the locked sector table) applied to a single EPS estimate.

2. **Maturity-Discount** — for revenue-positive, unprofitable growers
   (NBIS, CRDO at early stages). Formula:
       future_per_share = (revenue_T × mature_multiple) / (shares_today × (1 + dilution))
       pv               = future_per_share / (1 + r) ** years
   Bear/base/bull come from three revenue_T scenarios.

3. **Optionality** — for pre-revenue / pre-commercial (IONQ).
   The tool refuses to manufacture precision: it returns ``None`` for all
   three per-share values and asks the user to size as an option premium.

The router caller will hand us the auto-selector inputs (so that the picker
itself is a pure function and trivially testable).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum

from services.dd_coach.errors import DDEntryInvalid
from services.dd_coach.models import ValuationMethod

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults (locked V1 — see docs/DD_COACH_METHODOLOGY.md)
# ---------------------------------------------------------------------------

# Sector → (bear_multiple, base_multiple, bull_multiple). Used by both methods.
# Conservative ranges drawn from 5-year peer medians at locked-spec time
# (June 2026). Adjust only via ADR + methodology doc update (lockstep rule).
SECTOR_MULTIPLES_PSALES: dict[str, tuple[float, float, float]] = {
    "cloud-infra":          (6.0, 10.0, 14.0),
    "semis-ai-compute":     (8.0, 14.0, 20.0),
    "quantum-speculative":  (10.0, 20.0, 35.0),
}

SECTOR_MULTIPLES_PE: dict[str, tuple[float, float, float]] = {
    "cloud-infra":          (18.0, 28.0, 38.0),
    "semis-ai-compute":     (15.0, 25.0, 40.0),
    "quantum-speculative":  (25.0, 50.0, 80.0),
}

# Maturity-Discount defaults. Per the locked plan these are *advanced inputs*
# users almost never override.
DEFAULT_DISCOUNT_RATE = 0.12      # 12% — equity risk-adjusted
DEFAULT_DILUTION_PCT = 0.30       # +30% shares over the maturity window
DEFAULT_YEARS_TO_MATURITY = 4


# ---------------------------------------------------------------------------
# Auto-selector
# ---------------------------------------------------------------------------


class _Profitability(str, Enum):
    PROFITABLE_3YR = "profitable_3yr"
    REVENUE_BEARING = "revenue_bearing"
    PRE_REVENUE = "pre_revenue"


def _classify(
    revenue_latest: float | None,
    fcf_3yr_values: list[float | None],
    gross_margin_improving: bool,
) -> _Profitability:
    """Pure classifier — three buckets, locked spec."""
    if (
        len(fcf_3yr_values) >= 3
        and all((v is not None and v > 0) for v in fcf_3yr_values)
    ):
        return _Profitability.PROFITABLE_3YR
    # "revenue-bearing": >$50M sales AND improving GM (locked plan)
    if (
        revenue_latest is not None
        and revenue_latest > 50_000_000
        and gross_margin_improving
    ):
        return _Profitability.REVENUE_BEARING
    return _Profitability.PRE_REVENUE


def select_method(
    *,
    revenue_latest: float | None,
    fcf_3yr_values: list[float | None],
    gross_margin_improving: bool,
) -> ValuationMethod:
    """Return the locked V1 valuation method for these data-card signals."""
    cls = _classify(revenue_latest, fcf_3yr_values, gross_margin_improving)
    if cls is _Profitability.PROFITABLE_3YR:
        return ValuationMethod.MULTIPLE_BASED
    if cls is _Profitability.REVENUE_BEARING:
        return ValuationMethod.MATURITY_DISCOUNT
    return ValuationMethod.OPTIONALITY


# ---------------------------------------------------------------------------
# Method inputs + outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValuationRange:
    """Bear / base / bull per-share, with spot for context."""

    bear: float | None
    base: float | None
    bull: float | None
    spot: float | None

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)


@dataclass(frozen=True)
class ValuationOutput:
    method: ValuationMethod
    range: ValuationRange
    inputs_used: dict[str, float | None]
    rationale: str

    def to_dict(self) -> dict:
        return {
            "method": self.method.value,
            "range": self.range.to_dict(),
            "inputs_used": self.inputs_used,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class MultipleBasedInputs:
    forward_eps: float
    target_pe_low: float
    target_pe_mid: float
    target_pe_high: float
    spot_price: float | None = None


@dataclass(frozen=True)
class MaturityDiscountInputs:
    revenue_bear: float
    revenue_base: float
    revenue_bull: float
    mature_multiple: float
    shares_outstanding_today: float
    spot_price: float | None = None
    years_to_maturity: int = DEFAULT_YEARS_TO_MATURITY
    dilution_pct: float = DEFAULT_DILUTION_PCT
    discount_rate: float = DEFAULT_DISCOUNT_RATE


# ---------------------------------------------------------------------------
# Method implementations
# ---------------------------------------------------------------------------


def compute_multiple_based(inp: MultipleBasedInputs) -> ValuationOutput:
    """per-share = forward_eps × target_pe, for (low, mid, high) PEs."""
    if inp.forward_eps is None:
        raise DDEntryInvalid("multiple_based requires a forward_eps estimate.")
    if not (inp.target_pe_low <= inp.target_pe_mid <= inp.target_pe_high):
        raise DDEntryInvalid("target PEs must be ordered low ≤ mid ≤ high.")
    bear = inp.forward_eps * inp.target_pe_low
    base = inp.forward_eps * inp.target_pe_mid
    bull = inp.forward_eps * inp.target_pe_high
    return ValuationOutput(
        method=ValuationMethod.MULTIPLE_BASED,
        range=ValuationRange(bear=bear, base=base, bull=bull, spot=inp.spot_price),
        inputs_used={
            "forward_eps": inp.forward_eps,
            "target_pe_low": inp.target_pe_low,
            "target_pe_mid": inp.target_pe_mid,
            "target_pe_high": inp.target_pe_high,
        },
        rationale=(
            "Profitable, 3-yr FCF-positive business — valued on forward earnings × peer P/E range. "
            "Bear uses trough multiple, bull uses peak."
        ),
    )


def compute_maturity_discount(inp: MaturityDiscountInputs) -> ValuationOutput:
    """Discounts a future per-share equity value back to today.

    For each revenue scenario R_T:
      future_EV          = R_T × mature_multiple
      future_per_share   = future_EV / (shares_today × (1 + dilution_pct))
      present_per_share  = future_per_share / (1 + discount_rate)^years
    """
    if inp.shares_outstanding_today is None or inp.shares_outstanding_today <= 0:
        raise DDEntryInvalid("maturity_discount requires positive shares_outstanding_today.")
    if inp.mature_multiple <= 0:
        raise DDEntryInvalid("mature_multiple must be > 0.")
    if not (0 <= inp.dilution_pct < 5):
        raise DDEntryInvalid("dilution_pct should be a fraction (e.g. 0.30 for +30%).")
    if not (0 < inp.discount_rate < 1):
        raise DDEntryInvalid("discount_rate must be between 0 and 1 (e.g. 0.12).")
    if inp.years_to_maturity <= 0:
        raise DDEntryInvalid("years_to_maturity must be positive.")

    future_shares = inp.shares_outstanding_today * (1.0 + inp.dilution_pct)
    discount = (1.0 + inp.discount_rate) ** inp.years_to_maturity

    def _pv(revenue_t: float) -> float:
        future_ev = revenue_t * inp.mature_multiple
        future_ps = future_ev / future_shares
        return future_ps / discount

    return ValuationOutput(
        method=ValuationMethod.MATURITY_DISCOUNT,
        range=ValuationRange(
            bear=_pv(inp.revenue_bear),
            base=_pv(inp.revenue_base),
            bull=_pv(inp.revenue_bull),
            spot=inp.spot_price,
        ),
        inputs_used={
            "mature_multiple": inp.mature_multiple,
            "shares_outstanding_today": inp.shares_outstanding_today,
            "years_to_maturity": float(inp.years_to_maturity),
            "dilution_pct": inp.dilution_pct,
            "discount_rate": inp.discount_rate,
            "revenue_bear": inp.revenue_bear,
            "revenue_base": inp.revenue_base,
            "revenue_bull": inp.revenue_bull,
        },
        rationale=(
            "Revenue-bearing but unprofitable — discounting a maturity-year EV "
            f"(revenue × {inp.mature_multiple:.0f}x) back {inp.years_to_maturity}y "
            f"at {inp.discount_rate * 100:.0f}% and {inp.dilution_pct * 100:.0f}% dilution."
        ),
    )


def compute_optionality(spot_price: float | None = None) -> ValuationOutput:
    """Pre-revenue / binary — refuse to fabricate a price.

    Returns an all-None ``ValuationRange``. The frontend uses this as the
    signal to switch Q5 into the *"size as option premium"* path described
    in the methodology doc.
    """
    return ValuationOutput(
        method=ValuationMethod.OPTIONALITY,
        range=ValuationRange(bear=None, base=None, bull=None, spot=spot_price),
        inputs_used={},
        rationale=(
            "Pre-revenue / binary-outcome business — no defensible per-share fair value. "
            "Size this position as an option premium (money you can fully lose) and "
            "decide on conviction, not price."
        ),
    )


# ---------------------------------------------------------------------------
# Sector-default helpers
# ---------------------------------------------------------------------------


def default_sector_multiple_psales(sector: str) -> tuple[float, float, float] | None:
    """Locked sector P/Sales triples — used to pre-fill Maturity-Discount UI."""
    return SECTOR_MULTIPLES_PSALES.get(sector)


def default_sector_multiple_pe(sector: str) -> tuple[float, float, float] | None:
    return SECTOR_MULTIPLES_PE.get(sector)
