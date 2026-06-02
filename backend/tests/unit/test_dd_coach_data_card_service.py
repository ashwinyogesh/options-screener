"""Unit tests for services/dd_coach/data_card_service.py.

Uses a fake TickerProvider that yields a minimal ``FakeTicker`` exposing the
yfinance attributes the service reads (``info``, ``financials``, ``cashflow``,
``balance_sheet``). No network.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from services.dd_coach import data_card_service as dcs
from services.dd_coach.errors import DDEntryNotFound


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _annual_columns(start_year: int = 2023, n: int = 3) -> list[datetime]:
    """Return n-most-recent fiscal year-end timestamps, NEWEST FIRST.

    yfinance returns annual frames with columns ordered newest → oldest, so the
    fakes match that orientation.
    """
    return [datetime(start_year - i, 12, 31) for i in range(n)]


@dataclass
class FakeTicker:
    info: dict[str, Any]
    financials: pd.DataFrame | None
    cashflow: pd.DataFrame | None
    balance_sheet: pd.DataFrame | None
    quarterly_financials: pd.DataFrame | None = None
    quarterly_cashflow: pd.DataFrame | None = None


class FakeProvider:
    def __init__(self, tickers: dict[str, FakeTicker]) -> None:
        self._tickers = tickers

    def get(self, ticker: str) -> Any:
        if ticker not in self._tickers:
            return FakeTicker(info={}, financials=None, cashflow=None, balance_sheet=None)
        return self._tickers[ticker]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _msft_like() -> FakeTicker:
    """Profitable mature compounder: 3yr rev/FCF positive, no growth lens."""
    cols = _annual_columns(2025, 3)  # 2025, 2024, 2023
    fin = pd.DataFrame(
        {
            cols[0]: [245e9, 100e9],  # 2025
            cols[1]: [212e9, 88e9],   # 2024
            cols[2]: [198e9, 83e9],   # 2023
        },
        index=["Total Revenue", "Gross Profit"],
    )
    cf = pd.DataFrame(
        {
            cols[0]: [90e9, -16e9],
            cols[1]: [80e9, -15e9],
            cols[2]: [72e9, -13e9],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    bs = pd.DataFrame(
        {cols[0]: [76e9, 52e9]},
        index=["Cash And Cash Equivalents", "Total Debt"],
    )
    # Quarterly frames: 4 most recent quarters, newest-first (matches yfinance).
    qcols = [datetime(2025, 9, 30), datetime(2025, 6, 30), datetime(2025, 3, 31), datetime(2024, 12, 31)]
    qfin = pd.DataFrame(
        {qcols[0]: [70e9], qcols[1]: [65e9], qcols[2]: [62e9], qcols[3]: [60e9]},
        index=["Total Revenue"],
    )
    qcf = pd.DataFrame(
        {
            qcols[0]: [26e9, -5e9],
            qcols[1]: [24e9, -4e9],
            qcols[2]: [22e9, -4e9],
            qcols[3]: [20e9, -3e9],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    return FakeTicker(
        info={
            "longName": "Microsoft Corporation",
            "sector": "Technology",
            "industry": "Software—Infrastructure",
            "currentPrice": 412.5,
            "marketCap": 3.07e12,
            "priceToSalesTrailing12Months": 12.5,
            "trailingPE": 35.0,
            "ebitda": 130e9,
        },
        financials=fin,
        cashflow=cf,
        balance_sheet=bs,
        quarterly_financials=qfin,
        quarterly_cashflow=qcf,
    )


def _nbis_like() -> FakeTicker:
    """Growing, unprofitable: triggers Growth Lens + balance-sheet flag."""
    cols = _annual_columns(2025, 3)
    fin = pd.DataFrame(
        {
            cols[0]: [790e6, 380e6, 533e6],   # rev / gp / diluted shares (millions)
            cols[1]: [430e6, 180e6, 480e6],
            cols[2]: [120e6, 38e6,  410e6],
        },
        index=["Total Revenue", "Gross Profit", "Diluted Average Shares"],
    )
    cf = pd.DataFrame(
        {
            cols[0]: [-300e6, -120e6],  # OCF / capex → FCF = -420
            cols[1]: [-450e6, -130e6],  # FCF = -580
            cols[2]: [-220e6, -90e6],   # FCF = -310
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    bs = pd.DataFrame(
        {cols[0]: [2.4e9, 100e6]},
        index=["Cash And Cash Equivalents", "Total Debt"],
    )
    return FakeTicker(
        info={
            "longName": "Nebius Group N.V.",
            "sector": "Technology",
            "industry": "Software—Infrastructure",
            "currentPrice": 52.40,
            "marketCap": 12.1e9,
            "priceToSalesTrailing12Months": 15.3,
            "trailingPE": None,
            "ebitda": None,
        },
        financials=fin,
        cashflow=cf,
        balance_sheet=bs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProfitableCompany:
    @pytest.fixture
    def card(self) -> dcs.DataCard:
        provider = FakeProvider({"MSFT": _msft_like()})
        return dcs.get_data_card("msft", provider=provider)

    def test_basic_fields(self, card: dcs.DataCard) -> None:
        assert card.ticker == "MSFT"
        assert card.company_name == "Microsoft Corporation"
        assert card.sector == "Technology"
        assert card.spot_price == pytest.approx(412.5)
        assert card.market_cap == pytest.approx(3.07e12)

    def test_revenue_oldest_first(self, card: dcs.DataCard) -> None:
        years = [pt.year for pt in card.revenue_3yr]
        assert years == [2023, 2024, 2025]
        values = [pt.value for pt in card.revenue_3yr]
        assert values[0] < values[-1]  # growing

    def test_fcf_computed_as_ocf_minus_capex(self, card: dcs.DataCard) -> None:
        # 2025: 90 - |-16| = 74; 2024: 65; 2023: 59
        latest = card.fcf_3yr[-1]
        assert latest.year == 2025
        assert latest.value == pytest.approx(74e9)

    def test_no_balance_sheet_flag_for_healthy_business(self, card: dcs.DataCard) -> None:
        assert card.flags.balance_sheet_red is False
        assert card.flags.reasons == []

    def test_no_growth_lens_when_fcf_positive(self, card: dcs.DataCard) -> None:
        assert card.growth_lens is None

    def test_net_cash_position(self, card: dcs.DataCard) -> None:
        assert card.net_cash_position == pytest.approx(24e9)

    def test_revenue_ttm_sums_last_four_quarters(self, card: dcs.DataCard) -> None:
        # 70 + 65 + 62 + 60 = 257B
        assert card.revenue_ttm == pytest.approx(257e9)

    def test_fcf_ttm_sums_last_four_quarters(self, card: dcs.DataCard) -> None:
        # OCF (26+24+22+20)=92B; capex |-5-4-4-3|=16B; FCF = 76B
        assert card.fcf_ttm == pytest.approx(76e9)


class TestUnprofitableGrowthCompany:
    @pytest.fixture
    def card(self) -> dcs.DataCard:
        provider = FakeProvider({"NBIS": _nbis_like()})
        return dcs.get_data_card("NBIS", provider=provider)

    def test_balance_sheet_flag_triggers_on_negative_fcf_3yr(
        self, card: dcs.DataCard,
    ) -> None:
        assert card.flags.balance_sheet_red is True
        assert any("Negative free cash flow" in r for r in card.flags.reasons)

    def test_growth_lens_populated(self, card: dcs.DataCard) -> None:
        assert card.growth_lens is not None
        # Gross margin: 38/120 ≈ 32% → 380/790 ≈ 48% → improving.
        gms = [p.value for p in card.growth_lens.gross_margin_3yr if p.value is not None]
        assert gms[0] < gms[-1]
        assert "expanding" in card.growth_lens.summary.lower()

    def test_share_dilution_computed(self, card: dcs.DataCard) -> None:
        # 410M → 533M ≈ +30%
        assert card.growth_lens is not None
        dil = card.growth_lens.share_dilution_pct_3yr
        assert dil is not None
        assert dil == pytest.approx(0.30, rel=0.01)

    def test_cash_runway_years_reasonable(self, card: dcs.DataCard) -> None:
        # Avg burn ≈ (420+580+310)/3 = 437M  →  runway ≈ 2.4e9 / 437M ≈ 5.5y
        assert card.growth_lens is not None
        runway = card.growth_lens.cash_runway_years
        assert runway is not None
        assert 4.5 < runway < 7.0

    def test_ttm_none_when_no_quarterly_frames(self, card: dcs.DataCard) -> None:
        # _nbis_like doesn't populate quarterly frames → TTM should degrade to None.
        assert card.revenue_ttm is None
        assert card.fcf_ttm is None


class TestEmptyTicker:
    def test_no_data_raises_not_found(self) -> None:
        provider = FakeProvider({})  # any ticker yields blank FakeTicker
        with pytest.raises(DDEntryNotFound):
            dcs.get_data_card("UNKNOWN", provider=provider)
