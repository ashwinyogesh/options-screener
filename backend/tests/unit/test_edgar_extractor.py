"""Unit tests for `services.edgar.extractor.compute_pit_factors`.

Covers:
  - happy-path factor math
  - filing-date discipline (records filed AFTER asof are ignored)
  - dual-alias revenue: largest TTM wins (MSFT/NVDA bug fix)
  - plausibility guards: out-of-band ratios become None
  - empty / missing tags
"""
from __future__ import annotations

from datetime import date

import pytest

from services.edgar.extractor import (
    PIT_FACTORS,
    compute_pit_factors,
    latest_filing_lag_days,
)
from tests.fixtures.edgar import make_facts, make_facts_dual_revenue_alias


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_compute_pit_factors_happy_path() -> None:
    f = make_facts()
    asof = date(2024, 6, 30)  # well after 2024-02-15 filing
    spot = 50.0  # mcap = 50 * 1M shares = $50M

    out = compute_pit_factors(f, asof, spot_price=spot)

    assert out["rev_ttm"] == pytest.approx(100_000_000)
    assert out["ps_ttm"] == pytest.approx(0.5)
    assert out["ev_sales"] == pytest.approx(0.8)
    # EBITDA = OpInc + DA(=0) = 25M; EV = 50M + 30M = 80M
    assert out["ev_ebitda"] == pytest.approx(3.2)
    assert out["fcf_yield"] == pytest.approx(0.5)
    # ROIC = 25M * 0.79 / (40M + 80M) = 0.16458...
    assert out["roic_ttm"] == pytest.approx(25_000_000 * 0.79 / 120_000_000)
    assert out["debt_to_equity"] == pytest.approx(0.5)
    assert out["asset_turnover"] == pytest.approx(0.5)
    assert out["op_margin"] == pytest.approx(0.25)
    assert out["ni_margin"] == pytest.approx(0.20)
    assert out["nd_ebitda"] == pytest.approx(30_000_000 / 25_000_000)


def test_compute_pit_factors_returns_all_documented_keys() -> None:
    out = compute_pit_factors(make_facts(), date(2024, 6, 30), spot_price=50.0)
    for k in PIT_FACTORS:
        assert k in out, f"factor {k!r} missing from output"


# ---------------------------------------------------------------------------
# Filing-date discipline
# ---------------------------------------------------------------------------

def test_records_filed_after_asof_are_ignored() -> None:
    """asof = 2024-01-01 means only the 2023-02-15-filed (2022 FY) record is visible."""
    f = make_facts()
    out = compute_pit_factors(f, date(2024, 1, 1), spot_price=50.0)
    # Only the 2022 FY revenue (90M) should be visible
    assert out["rev_ttm"] == pytest.approx(90_000_000)
    # Op income for 2022 was not provided → ev_ebitda must be None (no EBITDA)
    assert out["ev_ebitda"] is None


def test_asof_before_any_filing_yields_nones() -> None:
    out = compute_pit_factors(make_facts(), date(2020, 1, 1), spot_price=50.0)
    # All ratio factors require at least one visible filing; with asof before
    # any filing, every derived ratio must be None. (rev_ttm / shares_pit /
    # net_debt are raw inputs and may legitimately be 0/None.)
    ratio_keys = (
        "fcf_yield", "ev_ebitda", "ev_sales", "ps_ttm", "roic_ttm",
        "nd_ebitda", "debt_to_equity", "asset_turnover", "ni_margin", "op_margin",
    )
    for k in ratio_keys:
        assert out[k] is None, f"{k} should be None for pre-coverage asof"


# ---------------------------------------------------------------------------
# Revenue dual-alias selection
# ---------------------------------------------------------------------------

def test_revenue_picks_largest_alias_not_first() -> None:
    """When both `Revenues` (small) and
    `RevenueFromContractWithCustomerExcludingAssessedTax` (large) are filed,
    the consolidated (larger) line must win.
    """
    f = make_facts_dual_revenue_alias()
    out = compute_pit_factors(f, date(2024, 6, 30), spot_price=50.0)
    # Largest alias = 100M, NOT the partial 5M Revenues line
    assert out["rev_ttm"] == pytest.approx(100_000_000)
    assert out["ps_ttm"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Plausibility guards
# ---------------------------------------------------------------------------

def test_implausible_ps_becomes_none() -> None:
    """Tiny shares-outstanding (XBRL units bug) → mcap tiny → PS << 0.05 → None."""
    f = make_facts()
    f["facts"]["us-gaap"]["CommonStockSharesOutstanding"]["units"]["shares"][0]["val"] = 100  # 100 shares
    out = compute_pit_factors(f, date(2024, 6, 30), spot_price=50.0)
    # mcap = 100 * 50 = $5K; PS = 5K / 100M = 5e-8 → guard trips → None
    assert out["ps_ttm"] is None
    # ev_sales also implausible (ev ≈ net_debt only) but EV-based survives if in band
    # ev = 30M / 100M = 0.3 → in [0.05, 100], should pass
    assert out["ev_sales"] is not None


def test_implausible_op_margin_becomes_none() -> None:
    f = make_facts()
    # Inflate op_income to 10x revenue → op_margin = 10.0 → outside [-5, 1]
    f["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"][0]["val"] = 1_000_000_000
    out = compute_pit_factors(f, date(2024, 6, 30), spot_price=50.0)
    assert out["op_margin"] is None


# ---------------------------------------------------------------------------
# spot_price optional
# ---------------------------------------------------------------------------

def test_no_spot_price_yields_none_for_market_cap_factors() -> None:
    out = compute_pit_factors(make_facts(), date(2024, 6, 30), spot_price=None)
    assert out["ps_ttm"] is None
    assert out["ev_sales"] is None
    assert out["ev_ebitda"] is None
    assert out["fcf_yield"] is None
    # Margins / leverage / asset turnover don't need spot
    assert out["op_margin"] == pytest.approx(0.25)
    assert out["debt_to_equity"] == pytest.approx(0.5)
    assert out["roic_ttm"] is not None


# ---------------------------------------------------------------------------
# Lag tracking
# ---------------------------------------------------------------------------

def test_latest_filing_lag_days() -> None:
    f = make_facts()
    # Most recent flow filing is 2024-02-15
    lag = latest_filing_lag_days(f, date(2024, 6, 30))
    assert lag == (date(2024, 6, 30) - date(2024, 2, 15)).days


def test_latest_filing_lag_returns_none_when_no_visible_filings() -> None:
    f = make_facts()
    lag = latest_filing_lag_days(f, date(2020, 1, 1))
    assert lag is None
