"""Unit tests for services/dd_coach/valuation_service.py.

Tests are pure-math; no Cosmos or yfinance dependencies.
"""
from __future__ import annotations

import math

import pytest

from services.dd_coach import valuation_service as vs
from services.dd_coach.errors import DDEntryInvalid
from services.dd_coach.models import ValuationMethod


# ---------------------------------------------------------------------------
# Auto-selector
# ---------------------------------------------------------------------------


class TestSelectMethod:
    def test_profitable_3yr_picks_multiple_based(self) -> None:
        method = vs.select_method(
            revenue_latest=200_000_000_000.0,
            fcf_3yr_values=[5e10, 6e10, 7e10],
            gross_margin_improving=True,
        )
        assert method is ValuationMethod.MULTIPLE_BASED

    def test_revenue_bearing_with_improving_gm_picks_maturity_discount(self) -> None:
        method = vs.select_method(
            revenue_latest=790_000_000.0,
            fcf_3yr_values=[-3.1e8, -5.8e8, -4.2e8],
            gross_margin_improving=True,
        )
        assert method is ValuationMethod.MATURITY_DISCOUNT

    def test_pre_revenue_picks_optionality(self) -> None:
        method = vs.select_method(
            revenue_latest=10_000_000.0,
            fcf_3yr_values=[-2e8, -2.5e8, -3e8],
            gross_margin_improving=False,
        )
        assert method is ValuationMethod.OPTIONALITY

    def test_only_one_positive_fcf_year_does_not_qualify_multiple_based(self) -> None:
        # Mixed history → not profitable-3yr; falls through to other buckets.
        method = vs.select_method(
            revenue_latest=80_000_000.0,
            fcf_3yr_values=[-1e7, 5e6, 8e6],
            gross_margin_improving=True,
        )
        assert method is ValuationMethod.MATURITY_DISCOUNT


# ---------------------------------------------------------------------------
# Multiple-based
# ---------------------------------------------------------------------------


class TestMultipleBased:
    def test_basic_math(self) -> None:
        out = vs.compute_multiple_based(
            vs.MultipleBasedInputs(
                forward_eps=12.0,
                target_pe_low=20.0,
                target_pe_mid=28.0,
                target_pe_high=36.0,
                spot_price=300.0,
            ),
        )
        assert out.method is ValuationMethod.MULTIPLE_BASED
        assert out.range.bear == pytest.approx(240.0)
        assert out.range.base == pytest.approx(336.0)
        assert out.range.bull == pytest.approx(432.0)
        assert out.range.spot == 300.0

    def test_out_of_order_pes_raises(self) -> None:
        with pytest.raises(DDEntryInvalid):
            vs.compute_multiple_based(
                vs.MultipleBasedInputs(
                    forward_eps=10.0,
                    target_pe_low=30.0,
                    target_pe_mid=20.0,
                    target_pe_high=40.0,
                ),
            )


# ---------------------------------------------------------------------------
# Maturity discount
# ---------------------------------------------------------------------------


class TestMaturityDiscount:
    def test_nbis_like_math(self) -> None:
        """Reproduces the worked example from the methodology doc:
        Bear 1.5B / Base 3.0B / Bull 5.0B @ 10x, 410M shares, +30%, 4y, 12%."""
        out = vs.compute_maturity_discount(
            vs.MaturityDiscountInputs(
                revenue_bear=1.5e9,
                revenue_base=3.0e9,
                revenue_bull=5.0e9,
                mature_multiple=10.0,
                shares_outstanding_today=410e6,
                spot_price=52.40,
                years_to_maturity=4,
                dilution_pct=0.30,
                discount_rate=0.12,
            ),
        )
        # future_shares = 533M  ;  discount = 1.12^4 = 1.5735
        future_shares = 410e6 * 1.30
        disc = 1.12 ** 4
        expected_bear = (1.5e9 * 10.0 / future_shares) / disc
        expected_base = (3.0e9 * 10.0 / future_shares) / disc
        expected_bull = (5.0e9 * 10.0 / future_shares) / disc
        assert out.range.bear == pytest.approx(expected_bear, rel=1e-6)
        assert out.range.base == pytest.approx(expected_base, rel=1e-6)
        assert out.range.bull == pytest.approx(expected_bull, rel=1e-6)
        # Spot-check absolute values from the doc (~18 / 36 / 59).
        assert math.isclose(out.range.bear, 17.8, abs_tol=1.0)
        assert math.isclose(out.range.base, 35.7, abs_tol=1.0)
        assert math.isclose(out.range.bull, 59.5, abs_tol=1.0)

    def test_zero_shares_raises(self) -> None:
        with pytest.raises(DDEntryInvalid):
            vs.compute_maturity_discount(
                vs.MaturityDiscountInputs(
                    revenue_bear=1e9, revenue_base=2e9, revenue_bull=3e9,
                    mature_multiple=10.0, shares_outstanding_today=0.0,
                ),
            )

    def test_negative_multiple_raises(self) -> None:
        with pytest.raises(DDEntryInvalid):
            vs.compute_maturity_discount(
                vs.MaturityDiscountInputs(
                    revenue_bear=1e9, revenue_base=2e9, revenue_bull=3e9,
                    mature_multiple=-5.0, shares_outstanding_today=4e8,
                ),
            )

    def test_invalid_discount_rate_raises(self) -> None:
        with pytest.raises(DDEntryInvalid):
            vs.compute_maturity_discount(
                vs.MaturityDiscountInputs(
                    revenue_bear=1e9, revenue_base=2e9, revenue_bull=3e9,
                    mature_multiple=10.0, shares_outstanding_today=4e8,
                    discount_rate=1.5,
                ),
            )


# ---------------------------------------------------------------------------
# Optionality
# ---------------------------------------------------------------------------


class TestOptionality:
    def test_returns_all_none_range(self) -> None:
        out = vs.compute_optionality(spot_price=8.42)
        assert out.method is ValuationMethod.OPTIONALITY
        assert out.range.bear is None
        assert out.range.base is None
        assert out.range.bull is None
        assert out.range.spot == 8.42
        assert "option premium" in out.rationale.lower()


# ---------------------------------------------------------------------------
# Sector defaults
# ---------------------------------------------------------------------------


class TestSectorDefaults:
    def test_known_sector_returns_triple(self) -> None:
        assert vs.default_sector_multiple_psales("cloud-infra") == (6.0, 10.0, 14.0)
        assert vs.default_sector_multiple_pe("cloud-infra") == (18.0, 28.0, 38.0)

    def test_unknown_sector_returns_none(self) -> None:
        assert vs.default_sector_multiple_psales("biotech") is None
        assert vs.default_sector_multiple_pe("biotech") is None
