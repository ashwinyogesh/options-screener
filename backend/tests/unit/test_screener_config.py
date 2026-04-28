"""
Compile-and-instantiation tests for the Phase 2 screener type surface.

These tests do *not* exercise any business logic — there isn't any in
`services.screener` yet. They verify:

1. The package and its public symbols import cleanly.
2. `Indicators` and `StrikeContext` accept the field set documented in the
   divergence map (CSP-shaped, CC-shaped, and DITM-shaped instances all build).
3. `ScreenerConfig` is constructible with stub callables that match the type
   aliases — i.e. the type surface is internally consistent.
4. `GateResult` short-circuit semantics (`passed=False`) carries a reason.
5. Default values for optional config fields (no pre_processors / hard_gates /
   tie_break_key / result_factory) work for CSP/CC-shaped configs.

If Phase 3 needs to evolve the type surface, these tests catch the breaking
change immediately.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.screener import (
    BaseScreenerResult,
    BaseStrikeResult,
    GateResult,
    Indicators,
    ScreenerConfig,
    StrikeBuildInputs,
    StrikeContext,
    SymbolMetrics,
)


# --- Indicator + StrikeContext shape ---------------------------------------

def _csp_indicators() -> Indicators:
    return Indicators(
        price=100.0,
        sma50=98.0,
        sma200=95.0,
        price_above_sma50=True,
        sma50_above_sma200=True,
        dist_from_52w_high_pct=-5.0,
        chain_median_oi=1200.0,
        earnings_within_dte=False,
        days_to_earnings=45,
        dte=30,
        hv_rank=70.0,
        iv_hv_ratio=1.4,
        iv_stale=False,
        rsi=55.0,
    )


def _ditm_indicators() -> Indicators:
    return Indicators(
        price=200.0,
        sma50=190.0,
        sma200=180.0,
        price_above_sma50=True,
        sma50_above_sma200=True,
        dist_from_52w_high_pct=-3.0,
        chain_median_oi=2000.0,
        earnings_within_dte=False,
        days_to_earnings=60,
        dte=120,
        hv_rank=35.0,
        weekly_rsi=58.0,
        ret_200d_frac=0.18,
        trend_pts=27.0,
        macro_hold=False,
    )


def test_indicators_csp_shape_builds():
    ind = _csp_indicators()
    assert ind.hv_rank == 70.0
    assert ind.weekly_rsi is None  # DITM-only field unset
    assert ind.macro_hold is False  # default


def test_indicators_ditm_shape_builds():
    ind = _ditm_indicators()
    assert ind.weekly_rsi == 58.0
    assert ind.iv_hv_ratio is None  # CSP/CC-only field unset
    assert ind.iv_stale is False  # default


def test_indicators_is_frozen():
    ind = _csp_indicators()
    with pytest.raises((AttributeError, Exception)):
        ind.price = 999.0  # type: ignore[misc]


def test_strike_context_csp_shape_builds():
    ctx = StrikeContext(
        delta=-0.22,
        strike=95.0,
        current_price=100.0,
        bid_ask_spread_pct=2.0,
        open_interest=500,
        volume=100,
        market_open=False,
        iv_used=0.30,
        dte=30,
        credit=1.50,
        vol_support_1=92.0,
        vol_support_2=88.0,
    )
    assert ctx.vol_support_1 == 92.0
    assert ctx.vol_resistance_1 is None  # CC-only field unset
    assert ctx.mid is None                # DITM-only field unset


def test_strike_context_ditm_shape_builds():
    ctx = StrikeContext(
        delta=0.82,
        strike=170.0,
        current_price=200.0,
        bid_ask_spread_pct=1.5,
        open_interest=800,
        volume=200,
        market_open=False,
        iv_used=0.25,
        dte=120,
        mid=33.5,
        extrinsic_pct_of_strike_frac=0.020,
        theta_annualized_pct=8.0,
        iv_percentile=40.0,
    )
    assert ctx.mid == 33.5
    assert ctx.credit is None             # CSP/CC-only field unset


# --- GateResult ------------------------------------------------------------

def test_gate_result_pass_default_reason():
    gr = GateResult(passed=True)
    assert gr.passed is True
    assert gr.reason == ""


def test_gate_result_failure_carries_reason():
    gr = GateResult(passed=False, reason="trend_pts<22")
    assert gr.passed is False
    assert gr.reason == "trend_pts<22"


# --- ScreenerConfig --------------------------------------------------------

def _stub_env_scorer(_ind: Indicators) -> tuple[float, str]:
    return 75.0, "stub"


def _stub_strike_scorer(_ctx: StrikeContext) -> tuple[float, str, dict[str, Any]]:
    return 80.0, "stub", {"roc_annualized": 12.0}


def _stub_delta_fn(_s: float, _k: float, _t: float, _sig: float, _r: float) -> float:
    return -0.22


def _stub_chain_fetcher(_sym: str, _lo: int, _hi: int) -> list[dict]:
    return []


def _stub_strike_filter(price: float, strike: float) -> bool:
    return strike < price * 1.02


def _stub_ohlc_fetcher(_sym: str, **_kw: Any) -> Any:
    return None


def _stub_iv_lookup(_chain: Any, _strike: float) -> float:
    return 0.30


def _stub_symbol_factory(_sym: str, _df: Any, _price: float) -> tuple[Indicators, SymbolMetrics]:
    return Indicators(
        price=100.0, sma50=99.0, sma200=98.0,
        price_above_sma50=True, sma50_above_sma200=True,
        dist_from_52w_high_pct=-2.0, chain_median_oi=0.0,
        earnings_within_dte=False, days_to_earnings=30, dte=0,
    ), SymbolMetrics()


def _stub_strike_context_builder(_inputs: StrikeBuildInputs, _ind: Indicators) -> StrikeContext:
    return StrikeContext(
        delta=-0.22, strike=95.0, current_price=100.0,
        bid_ask_spread_pct=2.0, open_interest=500, volume=100,
        market_open=False, iv_used=0.30, dte=30,
    )


def test_screener_config_csp_shape_builds_with_minimal_fields():
    cfg = ScreenerConfig(
        name="csp",
        direction="short_put",
        chain_fetcher=_stub_chain_fetcher,
        delta_fn=_stub_delta_fn,
        ohlc_fetcher=_stub_ohlc_fetcher,
        iv_lookup=_stub_iv_lookup,
        symbol_factory=_stub_symbol_factory,
        strike_context_builder=_stub_strike_context_builder,
        strike_filter=_stub_strike_filter,
        delta_range=(-0.35, -0.10),
        ideal_delta=-0.225,
        oi_delta_band=(-0.40, -0.10),
        env_scorer=_stub_env_scorer,
        strike_scorer=_stub_strike_scorer,
        final_blend=(0.4, 0.6),
    )
    # Optional fields default empty / None.
    assert cfg.pre_processors == ()
    assert cfg.hard_gates == ()
    assert cfg.tie_break_key is None
    assert cfg.result_factory is None


def test_screener_config_describe_output():
    cfg = ScreenerConfig(
        name="cc",
        direction="short_call",
        chain_fetcher=_stub_chain_fetcher,
        delta_fn=_stub_delta_fn,
        ohlc_fetcher=_stub_ohlc_fetcher,
        iv_lookup=_stub_iv_lookup,
        symbol_factory=_stub_symbol_factory,
        strike_context_builder=_stub_strike_context_builder,
        strike_filter=_stub_strike_filter,
        delta_range=(0.10, 0.35),
        ideal_delta=0.225,
        oi_delta_band=(0.10, 0.40),
        env_scorer=_stub_env_scorer,
        strike_scorer=_stub_strike_scorer,
        final_blend=(0.5, 0.5),
    )
    out = cfg.describe()
    assert "name=cc" in out
    assert "direction=short_call" in out
    assert "delta_range=(0.1, 0.35)" in out
    assert "blend=(0.5, 0.5)" in out


def test_screener_config_rejects_blend_weights_not_summing_to_one():
    with pytest.raises(ValueError, match="sum to ~1.0"):
        ScreenerConfig(
            name="bad",
            direction="short_put",
            chain_fetcher=_stub_chain_fetcher,
            delta_fn=_stub_delta_fn,
            ohlc_fetcher=_stub_ohlc_fetcher,
            iv_lookup=_stub_iv_lookup,
            symbol_factory=_stub_symbol_factory,
            strike_context_builder=_stub_strike_context_builder,
            strike_filter=_stub_strike_filter,
            delta_range=(-0.35, -0.10),
            ideal_delta=-0.225,
            oi_delta_band=(-0.40, -0.10),
            env_scorer=_stub_env_scorer,
            strike_scorer=_stub_strike_scorer,
            final_blend=(0.7, 0.7),
        )


def test_screener_config_rejects_negative_blend_weights():
    with pytest.raises(ValueError, match="non-negative"):
        ScreenerConfig(
            name="bad",
            direction="short_put",
            chain_fetcher=_stub_chain_fetcher,
            delta_fn=_stub_delta_fn,
            ohlc_fetcher=_stub_ohlc_fetcher,
            iv_lookup=_stub_iv_lookup,
            symbol_factory=_stub_symbol_factory,
            strike_context_builder=_stub_strike_context_builder,
            strike_filter=_stub_strike_filter,
            delta_range=(-0.35, -0.10),
            ideal_delta=-0.225,
            oi_delta_band=(-0.40, -0.10),
            env_scorer=_stub_env_scorer,
            strike_scorer=_stub_strike_scorer,
            final_blend=(-0.1, 1.1),
        )


def test_screener_config_is_frozen():
    cfg = ScreenerConfig(
        name="csp",
        direction="short_put",
        chain_fetcher=_stub_chain_fetcher,
        delta_fn=_stub_delta_fn,
        ohlc_fetcher=_stub_ohlc_fetcher,
        iv_lookup=_stub_iv_lookup,
        symbol_factory=_stub_symbol_factory,
        strike_context_builder=_stub_strike_context_builder,
        strike_filter=_stub_strike_filter,
        delta_range=(-0.35, -0.10),
        ideal_delta=-0.225,
        oi_delta_band=(-0.40, -0.10),
        env_scorer=_stub_env_scorer,
        strike_scorer=_stub_strike_scorer,
        final_blend=(0.4, 0.6),
    )
    with pytest.raises((AttributeError, Exception)):
        cfg.name = "mutated"  # type: ignore[misc]


def test_screener_config_ditm_shape_with_hooks():
    """DITM uses pre_processors and hard_gates; verify the tuple types compose."""

    def _stub_pre(_sym: str, _df: Any, ind: Indicators) -> Indicators:
        return ind  # no-op

    def _stub_gate(ind: Indicators) -> GateResult:
        if ind.trend_pts is not None and ind.trend_pts < 22:
            return GateResult(passed=False, reason="trend_pts<22")
        return GateResult(passed=True)

    def _ditm_tie_break(s: Any) -> float:
        return -abs(s.candidate.delta - 0.82) if hasattr(s, "candidate") else 0.0

    cfg = ScreenerConfig(
        name="ditm",
        direction="long_call",
        chain_fetcher=_stub_chain_fetcher,
        delta_fn=_stub_delta_fn,
        ohlc_fetcher=_stub_ohlc_fetcher,
        iv_lookup=_stub_iv_lookup,
        symbol_factory=_stub_symbol_factory,
        strike_context_builder=_stub_strike_context_builder,
        strike_filter=lambda p, k: k < p,
        delta_range=(0.70, 0.90),
        ideal_delta=0.82,
        oi_delta_band=(0.60, 0.95),
        env_scorer=_stub_env_scorer,
        strike_scorer=_stub_strike_scorer,
        final_blend=(0.5, 0.5),
        pre_processors=(_stub_pre,),
        hard_gates=(_stub_gate,),
        tie_break_key=_ditm_tie_break,
    )
    assert len(cfg.hard_gates) == 1
    gate = cfg.hard_gates[0]
    failing = Indicators(
        price=100.0, sma50=99.0, sma200=98.0,
        price_above_sma50=True, sma50_above_sma200=True,
        dist_from_52w_high_pct=-2.0, chain_median_oi=500.0,
        earnings_within_dte=False, days_to_earnings=30, dte=120,
        trend_pts=15.0,
    )
    result = gate(failing)
    assert result.passed is False
    assert "trend_pts" in result.reason


# --- Base result dataclasses -----------------------------------------------

def test_base_strike_result_default_is_best_false():
    r = BaseStrikeResult(
        strike=100.0,
        delta=-0.22,
        env_score=70.0,
        strike_score=80.0,
        final_score=76.0,
    )
    assert r.is_best is False
    assert r.env_detail == ""


def test_base_screener_result_default_best_score_zero():
    r = BaseScreenerResult(symbol="NVDA", price=500.0, dte=30, expiration="2026-05-15")
    assert r.best_score == 0.0
