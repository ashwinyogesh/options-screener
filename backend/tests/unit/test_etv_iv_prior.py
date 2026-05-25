"""Unit tests for the IV-prior + likelihood-ratio Bayesian update used by
the ETV asymmetry gate.

Covers:
  * lognormal cone math under known inputs
  * bucket-cutoff invariance to scenario relabelling / out-of-order prices
  * LR clamping at the [0.25, 4.0] bounds
  * Bayesian posterior normalisation (sums to 1.0)
  * horizon-string → days mapping
  * ``compute_posterior`` integration helper (graceful fallbacks)
  * end-to-end validator integration: probabilities overwritten with the
    IV-posterior, asymmetry recomputed, probability_check block surfaced
"""
from __future__ import annotations

import math

import pytest

from services.etv.iv_prior import (
    HORIZON_DAYS,
    LR_MAX,
    LR_MIN,
    apply_likelihood_ratios,
    compute_posterior,
    horizon_to_days,
    lognormal_scenario_probs,
)
from services.etv.validator import validate_report


# --------------------------------------------------------------- helpers

def _approx(actual: float, expected: float, tol: float = 1e-6) -> bool:
    return abs(actual - expected) <= tol


# ============================================================= lognormal


class TestLognormalScenarioProbs:
    """The IV-implied prior under a lognormal cone."""

    def test_probabilities_sum_to_one(self):
        p = lognormal_scenario_probs(
            spot=100.0, iv_annual=0.30, horizon_days=90,
            bear=80.0, base=105.0, bull=130.0,
        )
        assert _approx(sum(p), 1.0)
        assert all(0.0 <= x <= 1.0 for x in p)

    def test_symmetric_scenarios_around_spot_at_low_vol(self):
        # With very small drift (low IV, short horizon), bear / bull buckets
        # bracket symmetric scenarios with near-equal mass.
        spot, iv, T = 100.0, 0.05, 30
        p_bear, p_base, p_bull = lognormal_scenario_probs(
            spot=spot, iv_annual=iv, horizon_days=T,
            bear=spot * 0.90, base=spot, bull=spot * 1.10,
        )
        # Base bucket dominates — most mass near spot.
        assert p_base > p_bear and p_base > p_bull
        # Bear vs bull approximately symmetric (drift correction tiny).
        assert abs(p_bear - p_bull) < 0.02

    def test_high_iv_widens_tails(self):
        spot = 100.0
        low_iv = lognormal_scenario_probs(
            spot=spot, iv_annual=0.20, horizon_days=90,
            bear=70.0, base=100.0, bull=130.0,
        )
        high_iv = lognormal_scenario_probs(
            spot=spot, iv_annual=0.80, horizon_days=90,
            bear=70.0, base=100.0, bull=130.0,
        )
        # Higher vol → more tail mass, less base mass.
        assert high_iv[0] > low_iv[0]
        assert high_iv[2] > low_iv[2]
        assert high_iv[1] < low_iv[1]

    def test_handles_out_of_order_scenarios(self):
        # LLM emits bull < base < bear (degenerate).  Module should
        # still produce sensible probabilities by re-sorting internally.
        p = lognormal_scenario_probs(
            spot=100.0, iv_annual=0.30, horizon_days=90,
            bear=130.0, base=100.0, bull=80.0,
        )
        # bear (the highest price) gets the upper-tail mass; bull (lowest
        # price) gets the lower-tail mass.
        assert _approx(sum(p), 1.0)
        assert p[0] < p[1]  # bear (130) is upper tail, less mass than base
        assert p[2] < p[1]  # bull (80) is lower tail, less mass than base

    def test_rejects_invalid_inputs(self):
        with pytest.raises(ValueError):
            lognormal_scenario_probs(
                spot=0, iv_annual=0.3, horizon_days=90,
                bear=80, base=100, bull=120,
            )
        with pytest.raises(ValueError):
            lognormal_scenario_probs(
                spot=100, iv_annual=0, horizon_days=90,
                bear=80, base=100, bull=120,
            )
        with pytest.raises(ValueError):
            lognormal_scenario_probs(
                spot=100, iv_annual=0.3, horizon_days=0,
                bear=80, base=100, bull=120,
            )
        with pytest.raises(ValueError):
            lognormal_scenario_probs(
                spot=100, iv_annual=0.3, horizon_days=90,
                bear=0, base=100, bull=120,
            )

    def test_known_msft_like_case(self):
        # MSFT spot $420, iv30=22%, 90d horizon, bear $340 / base $430 / bull $510.
        # Cutoffs: c1 = 385, c2 = 470.  sigma_T = 0.22 * sqrt(90/365) ≈ 0.1092.
        # mu_T = -0.5 * 0.1092^2 ≈ -0.00597.
        # z1 = (ln(385/420) + 0.00597) / 0.1092 ≈ -0.7426 → Φ ≈ 0.229.
        # z2 = (ln(470/420) + 0.00597) / 0.1092 ≈  1.0838 → Φ ≈ 0.861.
        p = lognormal_scenario_probs(
            spot=420.0, iv_annual=0.22, horizon_days=90,
            bear=340.0, base=430.0, bull=510.0,
        )
        assert _approx(sum(p), 1.0, tol=1e-9)
        assert 0.15 < p[0] < 0.30   # bear ≈ 23%
        assert 0.55 < p[1] < 0.75   # base ≈ 63%
        assert 0.10 < p[2] < 0.20   # bull ≈ 14%


# =========================================================== LR / posterior


class TestApplyLikelihoodRatios:
    def test_neutral_lrs_return_prior(self):
        prior = (0.3, 0.5, 0.2)
        post, clamped = apply_likelihood_ratios(prior, (1.0, 1.0, 1.0))
        assert clamped == (1.0, 1.0, 1.0)
        for a, b in zip(post, prior):
            assert _approx(a, b)

    def test_clamps_extreme_lrs(self):
        prior = (0.3, 0.5, 0.2)
        # 10.0 should clamp to LR_MAX = 4.0; 0.01 to LR_MIN = 0.25.
        post, clamped = apply_likelihood_ratios(prior, (10.0, 1.0, 0.01))
        assert clamped == (LR_MAX, 1.0, LR_MIN)
        # Posterior weights base correctly given clamped values.
        z = LR_MAX * 0.3 + 1.0 * 0.5 + LR_MIN * 0.2
        assert _approx(post[0], LR_MAX * 0.3 / z)
        assert _approx(post[1], 1.0 * 0.5 / z)
        assert _approx(post[2], LR_MIN * 0.2 / z)

    def test_posterior_normalises(self):
        prior = (0.2, 0.6, 0.2)
        post, _ = apply_likelihood_ratios(prior, (2.0, 0.5, 3.0))
        assert _approx(sum(post), 1.0)
        # Bull was up-weighted (LR=3) and base down-weighted (LR=0.5);
        # bull share grows vs prior.
        assert post[2] > prior[2]
        assert post[1] < prior[1]

    def test_degenerate_zero_prior_returns_prior_unchanged(self):
        prior = (0.0, 0.0, 0.0)
        post, _ = apply_likelihood_ratios(prior, (1.0, 1.0, 1.0))
        assert post == prior


# ================================================================ horizon


class TestHorizonToDays:
    def test_known_horizons(self):
        assert horizon_to_days("short") == HORIZON_DAYS["short"]
        assert horizon_to_days("medium") == HORIZON_DAYS["medium"]
        assert horizon_to_days("long") == HORIZON_DAYS["long"]

    def test_case_insensitive(self):
        assert horizon_to_days("MEDIUM") == HORIZON_DAYS["medium"]
        assert horizon_to_days("Long") == HORIZON_DAYS["long"]

    def test_returns_none_for_unknown(self):
        assert horizon_to_days("hourly") is None
        assert horizon_to_days(None) is None
        assert horizon_to_days("") is None


# =========================================================== compute_posterior


class TestComputePosterior:
    """The validator-facing helper that bundles everything."""

    def _scenarios(self, **lrs) -> dict:
        return {
            "bear": {"price": 340.0, "likelihood_ratio": lrs.get("bear", 1.0)},
            "base": {"price": 430.0, "likelihood_ratio": lrs.get("base", 1.0)},
            "bull": {"price": 510.0, "likelihood_ratio": lrs.get("bull", 1.0)},
        }

    def test_returns_none_when_iv_missing(self):
        assert compute_posterior(
            spot=420.0, iv_annual=None, horizon_days=90,
            scenarios=self._scenarios(),
        ) is None

    def test_returns_none_when_horizon_missing(self):
        assert compute_posterior(
            spot=420.0, iv_annual=0.22, horizon_days=None,
            scenarios=self._scenarios(),
        ) is None

    def test_returns_none_when_spot_invalid(self):
        assert compute_posterior(
            spot=0, iv_annual=0.22, horizon_days=90,
            scenarios=self._scenarios(),
        ) is None

    def test_returns_none_when_price_invalid(self):
        bad = self._scenarios()
        bad["base"]["price"] = -10.0
        assert compute_posterior(
            spot=420.0, iv_annual=0.22, horizon_days=90, scenarios=bad,
        ) is None

    def test_lr_unity_yields_prior_as_posterior(self):
        out = compute_posterior(
            spot=420.0, iv_annual=0.22, horizon_days=90,
            scenarios=self._scenarios(),
        )
        assert out is not None
        assert out["lr_provided"] is True
        for s in ("bear", "base", "bull"):
            assert _approx(out["prior"][s], out["posterior"][s], tol=1e-9)

    def test_missing_lr_falls_back_to_unity(self):
        scns = self._scenarios()
        del scns["base"]["likelihood_ratio"]
        out = compute_posterior(
            spot=420.0, iv_annual=0.22, horizon_days=90, scenarios=scns,
        )
        assert out is not None
        assert out["lr_provided"] is False
        # Falls back silently → posterior == prior.
        for s in ("bear", "base", "bull"):
            assert _approx(out["prior"][s], out["posterior"][s], tol=1e-9)

    def test_extreme_lr_clamped(self):
        out = compute_posterior(
            spot=420.0, iv_annual=0.22, horizon_days=90,
            scenarios=self._scenarios(bull=99.0, bear=0.001),
        )
        assert out is not None
        assert out["lr_clamped"]["bull"] == LR_MAX
        assert out["lr_clamped"]["bear"] == LR_MIN

    def test_posterior_shifts_with_lr(self):
        out = compute_posterior(
            spot=420.0, iv_annual=0.22, horizon_days=90,
            scenarios=self._scenarios(bull=3.0, bear=0.5),
        )
        assert out is not None
        # Bull up-weighted → posterior bull > prior bull.
        assert out["posterior"]["bull"] > out["prior"]["bull"]
        assert out["posterior"]["bear"] < out["prior"]["bear"]
        assert _approx(sum(out["posterior"].values()), 1.0)


# =========================================================== validator


def _build_report(*, llm_probs: tuple[float, float, float],
                  lrs: tuple[float, float, float] | None,
                  prices: tuple[float, float, float] = (340.0, 430.0, 510.0)) -> dict:
    """Build a minimal report dict for the validator to chew on."""
    bear_px, base_px, bull_px = prices
    p_bear, p_base, p_bull = llm_probs

    def _scn(p_pct: float, px: float, lr: float | None) -> dict:
        out = {
            "probability_pct": p_pct,
            "price": px,
            "fundamental": px,
            "value_decomposition": {
                "fundamental": px,
                "regime_adjustment": 0,
                "market_expectations_adjustment": 0,
                "optionality": 0,
                "behavioral_premium": 0,
            },
            "derivation": [f"fundamental = {px}"],
            "conditions": [],
            "rationale": "r",
        }
        if lr is not None:
            out["likelihood_ratio"] = lr
            out["lr_rationale"] = "test"
        return out

    if lrs is None:
        lr_b = lr_m = lr_u = None
    else:
        lr_b, lr_m, lr_u = lrs

    econ_block = {
        "bear": _scn(p_bear, bear_px, lr_b),
        "base": _scn(p_base, base_px, lr_m),
        "bull": _scn(p_bull, bull_px, lr_u),
        "central_estimate": 0,
        "low_range": bear_px,
        "high_range": bull_px,
        "key_drivers": [],
        "key_sensitivities": [],
    }
    # ETV block mirrors econ (the validator enforces matching).
    etv_block = {
        "bear": _scn(p_bear, bear_px, lr_b),
        "base": _scn(p_base, base_px, lr_m),
        "bull": _scn(p_bull, bull_px, lr_u),
        "probability_weighted_etv": 0,
        "current_price": 420.0,
        "expected_return_pct": 0,
    }
    return {
        "economic_value": econ_block,
        "etv": etv_block,
        "asymmetry": {"upside_pct_weighted": 0, "downside_pct_weighted": 0, "ratio": 0},
        "decision": {"decision": "TRADE", "direction": "LONG", "confidence_pct": 75},
    }


class TestValidatorIVPosterior:
    def test_falls_back_to_llm_probs_when_iv_missing(self):
        report = _build_report(
            llm_probs=(30.0, 40.0, 30.0),
            lrs=(1.0, 1.0, 1.0),
        )
        validate_report(report, spot=420.0)
        # No iv_annual / horizon_days passed → method=llm_only.
        check = report["validation"].get("probability_check")
        assert check is not None
        assert check["method"] == "llm_only"
        # Original probabilities preserved.
        assert report["economic_value"]["bear"]["probability_pct"] == 30.0

    def test_overwrites_probs_with_posterior_when_iv_present(self):
        # LLM said 30/40/30 (favoring tails), LRs neutral, IV prior at
        # iv=0.22 / 90d puts most mass on base.
        report = _build_report(
            llm_probs=(30.0, 40.0, 30.0),
            lrs=(1.0, 1.0, 1.0),
        )
        validate_report(report, spot=420.0, iv_annual=0.22, horizon_days=90)
        check = report["validation"]["probability_check"]
        assert check["method"] == "iv_posterior"
        # Base probability should rise vs LLM's 40%.
        new_base = report["economic_value"]["base"]["probability_pct"]
        assert new_base > 50.0
        # Probabilities still sum to ~100.
        total = sum(report["economic_value"][s]["probability_pct"]
                    for s in ("bear", "base", "bull"))
        assert _approx(total, 100.0, tol=0.5)

    def test_lr_skew_moves_posterior(self):
        # LRs heavily skewed to bull.
        report = _build_report(
            llm_probs=(30.0, 40.0, 30.0),
            lrs=(0.5, 1.0, 3.0),
        )
        validate_report(report, spot=420.0, iv_annual=0.22, horizon_days=90)
        check = report["validation"]["probability_check"]
        assert check["posterior_pct"]["bull"] > check["prior_pct"]["bull"]
        assert check["posterior_pct"]["bear"] < check["prior_pct"]["bear"]

    def test_decision_relies_on_llm_view_flag(self):
        # IV prior alone gives NO TRADE (bull mass small).  LRs strongly
        # favour bull → posterior crosses ratio ≥ 2 threshold → flag.
        report = _build_report(
            llm_probs=(20.0, 50.0, 30.0),
            lrs=(0.25, 1.0, 4.0),
        )
        validate_report(report, spot=420.0, iv_annual=0.22, horizon_days=90)
        check = report["validation"]["probability_check"]
        # The flag is True iff the two decisions differ.
        diff = (
            check["decision_under_prior"]
            != check["decision_under_posterior"]
        )
        assert check["decision_relies_on_llm_view"] is diff

    def test_fragility_deducts_confidence(self):
        # Construct: LLM's raw probs vs posterior ratios differ by > 0.5.
        report = _build_report(
            llm_probs=(50.0, 30.0, 20.0),     # LLM: bear-heavy
            lrs=(0.25, 1.0, 4.0),             # LR pushes towards bull
        )
        validate_report(report, spot=420.0, iv_annual=0.22, horizon_days=90)
        check = report["validation"]["probability_check"]
        if check.get("decision_fragile"):
            # Confidence should have been deducted.
            assert report["decision"]["confidence_pct"] < 75
            deductions = report["decision"].get("confidence_deductions") or []
            assert any("posterior-vs-llm" in d for d in deductions)

    def test_no_lr_fields_uses_iv_prior_only(self):
        # LR not provided in scenarios → falls back to LR=1 (pure IV prior).
        report = _build_report(
            llm_probs=(30.0, 40.0, 30.0),
            lrs=None,
        )
        validate_report(report, spot=420.0, iv_annual=0.22, horizon_days=90)
        check = report["validation"]["probability_check"]
        assert check["method"] == "iv_posterior"
        assert check["lr_provided"] is False
        # Posterior == prior.
        for s in ("bear", "base", "bull"):
            assert _approx(check["posterior_pct"][s], check["prior_pct"][s], tol=0.5)

    def test_no_trade_when_posterior_ratio_below_two(self):
        # Pure IV prior at iv=0.22/90d concentrates mass at base ($430 ≈ spot)
        # → very small weighted up/down → ratio likely below 2.
        report = _build_report(
            llm_probs=(30.0, 40.0, 30.0),
            lrs=(1.0, 1.0, 1.0),
        )
        validate_report(report, spot=420.0, iv_annual=0.22, horizon_days=90)
        ratio = report["asymmetry"]["ratio"]
        if isinstance(ratio, (int, float)) and ratio < 2:
            assert report["decision"]["decision"] == "NO TRADE"
            assert report["decision"]["direction"] == "NEUTRAL"
