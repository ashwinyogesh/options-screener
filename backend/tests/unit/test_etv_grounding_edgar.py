"""Unit tests for the EDGAR supplement on ETV grounding.

Covers `_supplement_from_edgar` and the new `compute_raw_ttm_fundamentals`
extractor. No network or yfinance calls — `fundamentals_service` is
monkey-patched.
"""
from __future__ import annotations

from datetime import date

import pytest

from services.edgar.extractor import compute_raw_ttm_fundamentals
from services.etv import grounding as grounding_mod
from services.etv.grounding import EtvGrounding, _supplement_from_edgar
from tests.fixtures.edgar import make_facts


def _bare(**overrides) -> EtvGrounding:
    """Minimal EtvGrounding with most fields None — easy to seed for tests."""
    base = dict(
        ticker="TEST",
        company_name="Test Inc",
        sector=None,
        industry=None,
        business_summary=None,
        current_price=50.0,
        market_cap=50_000_000,
        enterprise_value=None,
        shares_out=None,
        week52_high=None,
        week52_low=None,
        avg_volume_10d=None,
        implied_vol_30d=None,
        short_pct_float=None,
        trailing_pe=None,
        forward_pe=None,
        ev_ebitda=None,
        ev_revenue=None,
        price_to_fcf=None,
        price_to_book=None,
        revenue_ttm=None,
        revenue_growth_yoy=None,
        gross_margin=None,
        ebitda=None,
        ebitda_margin=None,
        operating_income=None,
        operating_margin=None,
        net_income=None,
        eps_ttm=None,
        free_cash_flow=None,
        total_debt=None,
        net_debt=None,
        cash=None,
        capex=None,
        roic=None,
        forward_revenue=None,
        forward_eps=None,
        long_term_growth=None,
        analyst_count=None,
        analyst_recommendation=None,
        analyst_target_mean=None,
        analyst_target_high=None,
        analyst_target_low=None,
        sma_50=None,
        sma_200=None,
        rsi_14=None,
        as_of="2024-06-30",
    )
    base.update(overrides)
    return EtvGrounding(**base)


# ---------------------------------------------------------------------------
# compute_raw_ttm_fundamentals
# ---------------------------------------------------------------------------

def test_raw_ttm_extracts_line_items_from_synthetic_facts() -> None:
    raw = compute_raw_ttm_fundamentals(make_facts(), date(2024, 6, 30))
    # Annual 2023 record is what `_value_flow_ttm` should select at 2024-06-30.
    assert raw["revenue_ttm"] == pytest.approx(100_000_000)
    assert raw["operating_income"] == pytest.approx(25_000_000)
    assert raw["net_income"] == pytest.approx(20_000_000)
    assert raw["free_cash_flow"] == pytest.approx(25_000_000)  # 30M op_cf - 5M capex
    assert raw["capex"] == pytest.approx(5_000_000)
    # Synthetic fixture has no D&A → ebitda == op_income
    assert raw["ebitda"] == pytest.approx(25_000_000)
    assert raw["operating_margin"] == pytest.approx(0.25)
    assert raw["ebitda_margin"] == pytest.approx(0.25)
    assert raw["cash"] == pytest.approx(10_000_000)
    assert raw["total_debt"] == pytest.approx(40_000_000)
    assert raw["net_debt"] == pytest.approx(30_000_000)
    assert raw["shares_out"] == pytest.approx(1_000_000)
    # roic = op_income * 0.79 / (debt + equity) = 25M*0.79 / 120M ≈ 0.1646
    assert raw["roic"] == pytest.approx(0.16458333, rel=1e-4)


def test_raw_ttm_returns_none_when_facts_empty() -> None:
    raw = compute_raw_ttm_fundamentals({"facts": {"us-gaap": {}}}, date(2024, 6, 30))
    assert all(v is None for v in raw.values())


# ---------------------------------------------------------------------------
# _supplement_from_edgar
# ---------------------------------------------------------------------------

def test_supplement_fills_none_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = compute_raw_ttm_fundamentals(make_facts(), date(2024, 6, 30))
    monkeypatch.setattr(
        grounding_mod.fundamentals_service,
        "get_raw_fundamentals",
        lambda ticker, asof: raw,
    )
    g = _bare()
    supplemented = _supplement_from_edgar(g)

    assert supplemented.revenue_ttm == pytest.approx(100_000_000)
    assert supplemented.operating_income == pytest.approx(25_000_000)
    assert supplemented.free_cash_flow == pytest.approx(25_000_000)
    assert supplemented.capex == pytest.approx(5_000_000)
    assert supplemented.shares_out == pytest.approx(1_000_000)
    assert supplemented.operating_margin == pytest.approx(0.25)


def test_supplement_never_overwrites_existing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = compute_raw_ttm_fundamentals(make_facts(), date(2024, 6, 30))
    monkeypatch.setattr(
        grounding_mod.fundamentals_service,
        "get_raw_fundamentals",
        lambda ticker, asof: raw,
    )
    # Seed yfinance values that disagree with EDGAR — they must win.
    g = _bare(
        revenue_ttm=999.0,
        operating_income=888.0,
        operating_margin=0.99,
        free_cash_flow=777.0,
    )
    supplemented = _supplement_from_edgar(g)

    assert supplemented.revenue_ttm == 999.0
    assert supplemented.operating_income == 888.0
    assert supplemented.operating_margin == 0.99
    assert supplemented.free_cash_flow == 777.0
    # But a None slot is still filled.
    assert supplemented.shares_out == pytest.approx(1_000_000)


def test_supplement_is_noop_when_edgar_returns_all_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        grounding_mod.fundamentals_service,
        "get_raw_fundamentals",
        lambda ticker, asof: {k: None for k in (
            "revenue_ttm", "operating_income", "operating_margin", "ebitda",
            "ebitda_margin", "net_income", "ni_margin", "free_cash_flow",
            "capex", "cash", "total_debt", "net_debt", "shares_out", "roic",
        )},
    )
    g = _bare()
    out = _supplement_from_edgar(g)
    assert out is g  # unchanged identity


def test_supplement_swallows_facade_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(ticker, asof):
        raise RuntimeError("simulated EDGAR outage")

    monkeypatch.setattr(
        grounding_mod.fundamentals_service,
        "get_raw_fundamentals",
        _boom,
    )
    g = _bare(revenue_ttm=42.0)
    out = _supplement_from_edgar(g)
    # No crash; original grounding returned unchanged.
    assert out is g
    assert out.revenue_ttm == 42.0
