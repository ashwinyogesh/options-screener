"""Tests for `services.scoring.ditm_v4_pipeline`.

Exercises the full pipeline integration: fundamentals lookup is stubbed,
results are passed through the cross-sectional scorer, and the v4
fields plus the legacy two-pillar percentiles are written back.
"""
from __future__ import annotations

from datetime import date

import pytest

from services import fundamentals_service
from services.ditm_service import DitmResult, DitmStrikeResult
from services.scoring.ditm_v4_pipeline import apply_v4_scoring


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strike(strike: float, delta: float, mid: float, ext_pct: float) -> DitmStrikeResult:
    return DitmStrikeResult(
        strike=strike,
        delta=delta,
        mid=mid,
        extrinsic_pct=ext_pct,
        theta_annualized_pct=2.0,
        breakeven_pct=0.0,
        capital_efficiency_pct=10.0,
        bid_ask_spread_pct=1.5,
        chain_oi=200,
        env_score=50.0,
        strike_score=50.0,
        ditm_score=50.0,
        env_detail="placeholder",
        strike_detail="placeholder",
        is_best=False,
        iv_fallback=False,
    )


def _make_result(symbol: str, price: float, *, weekly_rsi: float = 60.0,
                 dist52w: float = -10.0, hv30: float = 30.0,
                 ret_200d: float = 15.0) -> DitmResult:
    return DitmResult(
        symbol=symbol,
        price=price,
        sma_ratio=1.05,
        hv_rank=40.0,
        hv30=hv30,
        weekly_rsi=weekly_rsi,
        ret_200d=ret_200d,
        dist_from_52w_high_pct=dist52w,
        earnings_date=None,
        days_to_earnings=None,
        earnings_within_dte=False,
        dte=120,
        expiration="2026-09-18",
        strikes=[
            _make_strike(price * 0.85, 0.85, price * 0.18, 3.0),
            _make_strike(price * 0.80, 0.88, price * 0.22, 2.0),
        ],
        best_ditm_score=50.0,
    )


@pytest.fixture
def stub_fundamentals(monkeypatch: pytest.MonkeyPatch):
    """Stub `fundamentals_service.get_pit_factors` per-ticker."""
    table: dict[str, dict[str, float | None]] = {}

    def _stub(ticker: str, _asof, spot_price=None):
        return table.get(ticker, {
            "ps_ttm": None, "ev_sales": None, "ev_ebitda": None,
            "debt_to_equity": None, "nd_ebitda": None,
        })

    monkeypatch.setattr(fundamentals_service, "get_pit_factors", _stub)
    return table


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------

def test_apply_v4_scoring_handles_empty() -> None:
    assert apply_v4_scoring([], asof=date(2026, 5, 21)) == []


def test_apply_v4_scoring_writes_tier_and_score_fields(stub_fundamentals) -> None:
    """Happy path: fundamentals stubbed for two tickers, scores computed."""
    stub_fundamentals["AAPL"] = {
        "ps_ttm": 7.0, "ev_sales": 7.0, "ev_ebitda": 18.0,
        "debt_to_equity": 1.5, "nd_ebitda": 1.0,
    }
    stub_fundamentals["MSFT"] = {
        "ps_ttm": 12.0, "ev_sales": 12.0, "ev_ebitda": 25.0,
        "debt_to_equity": 0.5, "nd_ebitda": -0.5,
    }
    results = [
        _make_result("AAPL", 200.0),
        _make_result("MSFT", 400.0, weekly_rsi=55.0),
    ]
    out = apply_v4_scoring(results, asof=date(2026, 5, 21))
    assert out is results  # mutates in-place

    # Every strike got a v4 score and tier.
    for r in results:
        for s in r.strikes:
            assert s.score_v4 is not None
            assert 0.0 <= s.score_v4 <= 100.0
            assert s.tier in {"A", "B", "C", "D", "E"}
            # Legacy fields synthesized
            assert isinstance(s.env_detail, str)
            assert "Val" in s.env_detail or "Cap" in s.env_detail
            assert "Tech" in s.strike_detail or "Opt" in s.strike_detail
        assert r.best_tier in {"A", "B", "C", "D", "E"}
        assert r.best_ditm_score == max(s.ditm_score for s in r.strikes)


def test_apply_v4_scoring_overwrites_ditm_score_with_v4(stub_fundamentals) -> None:
    """ditm_score field carries v4 percentile, not the v3 placeholder."""
    stub_fundamentals["AAPL"] = {
        "ps_ttm": 5.0, "ev_sales": 5.0, "ev_ebitda": 15.0,
        "debt_to_equity": 1.2, "nd_ebitda": 0.8,
    }
    stub_fundamentals["GOOG"] = {
        "ps_ttm": 6.0, "ev_sales": 6.0, "ev_ebitda": 16.0,
        "debt_to_equity": 0.8, "nd_ebitda": 0.2,
    }
    results = [_make_result("AAPL", 200.0), _make_result("GOOG", 150.0)]
    apply_v4_scoring(results, asof=date(2026, 5, 21))
    # ditm_score must equal score_v4 for eligible strikes (not the
    # placeholder 50.0 we initialised with).
    for r in results:
        for s in r.strikes:
            if s.score_v4 is not None:
                assert s.ditm_score == s.score_v4


def test_apply_v4_scoring_recomputes_is_best_flag(stub_fundamentals) -> None:
    """The strike with the top v4 score must be flagged is_best=True."""
    stub_fundamentals["AAPL"] = {
        "ps_ttm": 5.0, "ev_sales": 5.0, "ev_ebitda": 15.0,
        "debt_to_equity": 1.0, "nd_ebitda": 0.5,
    }
    stub_fundamentals["TSLA"] = {
        "ps_ttm": 9.0, "ev_sales": 9.0, "ev_ebitda": 22.0,
        "debt_to_equity": 0.3, "nd_ebitda": -1.0,
    }
    results = [_make_result("AAPL", 200.0), _make_result("TSLA", 250.0)]
    apply_v4_scoring(results, asof=date(2026, 5, 21))
    for r in results:
        best_count = sum(1 for s in r.strikes if s.is_best)
        assert best_count == 1
        best_strike = next(s for s in r.strikes if s.is_best)
        assert best_strike.ditm_score == r.best_ditm_score


def test_apply_v4_scoring_factor_breakdown_present(stub_fundamentals) -> None:
    """factor_breakdown maps each of the 13 factors to a signed contribution."""
    stub_fundamentals["AAPL"] = {
        "ps_ttm": 5.0, "ev_sales": 5.0, "ev_ebitda": 15.0,
        "debt_to_equity": 1.0, "nd_ebitda": 0.5,
    }
    stub_fundamentals["MSFT"] = {
        "ps_ttm": 8.0, "ev_sales": 8.0, "ev_ebitda": 20.0,
        "debt_to_equity": 0.5, "nd_ebitda": -0.5,
    }
    results = [_make_result("AAPL", 200.0), _make_result("MSFT", 400.0)]
    apply_v4_scoring(results, asof=date(2026, 5, 21))
    expected_factors = {
        "ps_ttm", "ev_sales", "ev_ebitda",
        "debt_to_equity", "nd_ebitda",
        "wk_rsi", "dist52w", "hv30", "ret_200d",
        "sector_rs_6m",
        "leverage", "delta", "extrinsic_pct",
    }
    for r in results:
        for s in r.strikes:
            assert set(s.factor_breakdown.keys()) == expected_factors


def test_apply_v4_scoring_falls_back_when_fundamentals_raise(monkeypatch) -> None:
    """A fundamentals fetch failure for one ticker must not abort the pass."""
    def _boom(ticker, _asof, spot_price=None):
        raise RuntimeError("EDGAR down")

    monkeypatch.setattr(fundamentals_service, "get_pit_factors", _boom)
    results = [_make_result("AAPL", 200.0), _make_result("MSFT", 400.0)]
    out = apply_v4_scoring(results, asof=date(2026, 5, 21))
    # Pass completed without raising; eligible strikes still have technical
    # + option factors observed (>= 8 by construction here: 4 tech + 3 opt
    # = 7 — short by 1 of MIN_FACTORS_OBSERVED, so score=None expected).
    for r in out:
        for s in r.strikes:
            # Ineligible row: score_v4 is None, ditm_score retains v3 value
            # (50.0 from our test setup).
            assert s.score_v4 is None
            assert s.tier is None
