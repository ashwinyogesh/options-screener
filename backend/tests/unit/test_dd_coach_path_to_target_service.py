"""Unit tests for services/dd_coach/path_to_target_service.py.

Uses a FakeProvider that yields a minimal yfinance-shaped object. No network.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from services.dd_coach import path_to_target_service as pts
from services.dd_coach.errors import DDEntryInvalid, DDEntryNotFound


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _cols(start_year: int = 2025, n: int = 3) -> list[datetime]:
    return [datetime(start_year - i, 12, 31) for i in range(n)]


@dataclass
class FakeTicker:
    info: dict[str, Any]
    financials: pd.DataFrame | None
    cashflow: pd.DataFrame | None


class FakeProvider:
    def __init__(self, tickers: dict[str, FakeTicker]) -> None:
        self._t = tickers

    def get(self, ticker: str) -> Any:
        return self._t.get(
            ticker,
            FakeTicker(info={}, financials=None, cashflow=None),
        )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _profitable_tech() -> FakeTicker:
    """MSFT-shaped: positive EPS, positive FCF, tech sector, 10% revenue CAGR."""
    cols = _cols(2025, 3)
    fin = pd.DataFrame(
        {
            cols[0]: [242e9],  # 2025
            cols[1]: [220e9],  # 2024
            cols[2]: [200e9],  # 2023  → CAGR ≈ 10%
        },
        index=["Total Revenue"],
    )
    cf = pd.DataFrame(
        {
            cols[0]: [90e9, -16e9],
            cols[1]: [80e9, -15e9],
            cols[2]: [72e9, -13e9],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    return FakeTicker(
        info={
            "sector": "Technology",
            "currentPrice": 400.0,
            "sharesOutstanding": 7.5e9,
            "trailingEps": 12.0,        # > 2% of rev/share (~$32)
            "totalRevenue": 242e9,
        },
        financials=fin,
        cashflow=cf,
    )


def _earnings_pos_fcf_neg() -> FakeTicker:
    """Positive earnings, negative FCF — should still pick 'earnings' basis."""
    cols = _cols(2025, 3)
    fin = pd.DataFrame(
        {cols[0]: [10e9], cols[1]: [9e9], cols[2]: [8e9]},
        index=["Total Revenue"],
    )
    cf = pd.DataFrame(
        {
            cols[0]: [1e9, -3e9],  # OCF − big capex → FCF negative
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    return FakeTicker(
        info={
            "sector": "Industrials",
            "currentPrice": 50.0,
            "sharesOutstanding": 1e9,
            "trailingEps": 2.0,        # 2/10 = 20% of rev/share
            "totalRevenue": 10e9,
        },
        financials=fin,
        cashflow=cf,
    )


def _both_negative() -> FakeTicker:
    """Loss-making + negative FCF — no cash basis available."""
    cols = _cols(2025, 2)
    fin = pd.DataFrame(
        {cols[0]: [2e9], cols[1]: [1e9]},
        index=["Total Revenue"],
    )
    cf = pd.DataFrame(
        {cols[0]: [-200e6, -100e6]},
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    return FakeTicker(
        info={
            "sector": "Technology",
            "currentPrice": 30.0,
            "sharesOutstanding": 500e6,
            "trailingEps": -0.5,
            "totalRevenue": 2e9,
        },
        financials=fin,
        cashflow=cf,
    )


def _unknown_sector() -> FakeTicker:
    fin = pd.DataFrame({_cols(2025, 1)[0]: [5e9]}, index=["Total Revenue"])
    cf = pd.DataFrame(
        {_cols(2025, 1)[0]: [1e9, -100e6]},
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    return FakeTicker(
        info={
            "sector": None,
            "currentPrice": 100.0,
            "sharesOutstanding": 1e9,
            "trailingEps": 5.0,
            "totalRevenue": 5e9,
        },
        financials=fin,
        cashflow=cf,
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(
        {
            "MSFT": _profitable_tech(),
            "EARN": _earnings_pos_fcf_neg(),
            "LOSS": _both_negative(),
            "MYST": _unknown_sector(),
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_profitable_tech_easy_target(self, provider: FakeProvider) -> None:
        """Spot 400 → target 420 (5% return). All three paths should be 'easy'."""
        r = pts.get_path_to_target("MSFT", 420.0, provider=provider)
        assert r.spot == 400.0
        assert r.target == 420.0
        assert r.cash_basis == "earnings"
        assert r.cash_per_share == 12.0
        assert r.current_multiple is not None
        assert abs(r.current_multiple - (400.0 / 12.0)) < 1e-6
        assert r.path_a_growth_only.applicable
        assert r.path_b_multiple_only.applicable
        assert r.path_c_mixed.applicable
        assert r.path_a_growth_only.realism == "easy"
        # 5% return → required growth ≈ 5%, well under 10% historical → easy
        assert abs((r.path_a_growth_only.required_growth_pct or 0) - 0.05) < 1e-6

    def test_profitable_tech_stretch_target(self, provider: FakeProvider) -> None:
        """Spot 400 → target 1000 (150% return). Should NOT be easy on all paths."""
        r = pts.get_path_to_target("MSFT", 1000.0, provider=provider)
        # 150% growth vs ~10% historical → > 3x baseline → unrealistic
        assert r.path_a_growth_only.realism == "unrealistic"
        # required multiple ≈ 1000/12 = 83×, peer high ~28× → > 1.5x → unrealistic
        assert r.path_b_multiple_only.realism == "unrealistic"


class TestEarningsVsFcf:
    def test_earnings_pos_fcf_neg_picks_earnings(self, provider: FakeProvider) -> None:
        r = pts.get_path_to_target("EARN", 60.0, provider=provider)
        assert r.cash_basis == "earnings"
        assert r.cash_per_share == 2.0
        assert r.path_a_growth_only.applicable

    def test_both_negative_disables_growth_paths(self, provider: FakeProvider) -> None:
        r = pts.get_path_to_target("LOSS", 40.0, provider=provider)
        assert r.cash_basis is None
        assert r.cash_per_share is None
        assert r.path_a_growth_only.applicable is False
        assert r.path_b_multiple_only.applicable is False
        assert r.path_c_mixed.applicable is False
        assert any("no positive per-share cash" in n.lower() for n in r.notes)


class TestEdgeCases:
    def test_unknown_sector_uses_generic_band(self, provider: FakeProvider) -> None:
        r = pts.get_path_to_target("MYST", 110.0, provider=provider)
        assert r.peer_label == "Broad-market peers"
        assert r.peer_multiple_low == 15.0
        assert r.peer_multiple_high == 22.0
        assert any("sector unknown" in n.lower() for n in r.notes)

    def test_invalid_target_price_raises(self, provider: FakeProvider) -> None:
        with pytest.raises(DDEntryInvalid):
            pts.get_path_to_target("MSFT", 0.0, provider=provider)
        with pytest.raises(DDEntryInvalid):
            pts.get_path_to_target("MSFT", -5.0, provider=provider)

    def test_unknown_ticker_raises_not_found(self, provider: FakeProvider) -> None:
        with pytest.raises(DDEntryNotFound):
            pts.get_path_to_target("ZZZZ", 100.0, provider=provider)
