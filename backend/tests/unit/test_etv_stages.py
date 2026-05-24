"""Unit tests for the S1 (audit) and S2 (intrinsic) ETV stages.

LLM and yfinance are stubbed out: ``call_json`` is monkeypatched on each
stage module so we exercise the prompt-building / payload-trimming /
guard-wiring code without any network round-trips.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import pytest

from services.etv import orchestrator
from services.etv.schemas import (
    S1_AUDIT_SCHEMA,
    S2_INTRINSIC_SCHEMA,
    S3_OVERLAYS_SCHEMA,
    S4_DECISION_SCHEMA,
    S5_CRITIC_SCHEMA,
)
from services.etv.stages import (
    s1_audit, s2_intrinsic, s3_overlays, s4_decision, s5_critic,
)
from services.etv.stages._base import StageResult


# ----------------------------------------------------------- fixtures ---


@dataclass
class _G:
    """Minimal grounding double matching ``EtvGrounding`` field surface."""
    ticker: str = "MSFT"
    company_name: str = "Microsoft"
    sector: Optional[str] = "Technology"
    industry: Optional[str] = "Software"
    business_summary: Optional[str] = "n/a"
    current_price: float = 420.0
    market_cap: Optional[float] = 3_100_000_000_000.0
    enterprise_value: Optional[float] = 3_050_000_000_000.0
    shares_out: Optional[float] = 7_400_000_000.0
    week52_high: Optional[float] = 470.0
    week52_low: Optional[float] = 310.0
    avg_volume_10d: Optional[float] = 20_000_000.0
    implied_vol_30d: Optional[float] = 0.22
    short_pct_float: Optional[float] = 0.005
    trailing_pe: Optional[float] = 34.0
    forward_pe: Optional[float] = 32.0
    ev_ebitda: Optional[float] = 24.0
    ev_revenue: Optional[float] = 12.0
    price_to_fcf: Optional[float] = 38.0
    price_to_book: Optional[float] = 12.0
    revenue_ttm: Optional[float] = 245_000_000_000.0
    revenue_growth_yoy: Optional[float] = 0.12
    gross_margin: Optional[float] = 0.69
    ebitda: Optional[float] = 130_000_000_000.0
    ebitda_margin: Optional[float] = 0.53
    operating_income: Optional[float] = 108_000_000_000.0
    operating_margin: Optional[float] = 0.44
    net_income: Optional[float] = 88_000_000_000.0
    eps_ttm: Optional[float] = 11.9
    free_cash_flow: Optional[float] = 74_000_000_000.0
    total_debt: Optional[float] = 55_000_000_000.0
    net_debt: Optional[float] = -20_000_000_000.0
    cash: Optional[float] = 75_000_000_000.0
    capex: Optional[float] = 44_000_000_000.0
    roic: Optional[float] = 0.22
    forward_revenue: Optional[float] = 280_000_000_000.0
    forward_eps: Optional[float] = 13.5
    long_term_growth: Optional[float] = 0.11
    analyst_count: Optional[int] = 35
    analyst_recommendation: Optional[str] = "buy"
    analyst_target_mean: Optional[float] = 480.0
    analyst_target_high: Optional[float] = 600.0
    analyst_target_low: Optional[float] = 380.0
    sma_50: Optional[float] = 410.0
    sma_200: Optional[float] = 390.0
    rsi_14: Optional[float] = 58.0
    as_of: str = "2026-05-23"


@pytest.fixture
def g() -> _G:
    return _G()


# ----------------------------------------------------------- S1 tests ---


class TestS1Audit:
    def test_returns_stage_result_with_no_guard(self, monkeypatch, g):
        canned = {
            "missing_inputs": [
                "long_term_growth: ASSUMPTION used = 0.10 (sector median)",
            ],
            "model_archetype": "Growth",
            "archetype_rationale": "Double-digit revenue growth + high reinvestment.",
            "primary_model": "DCF",
            "model_rationale": "Cash-generative; FCF-based valuation appropriate.",
            "required_inputs": ["revenue_ttm", "free_cash_flow",
                                "shares_out", "long_term_growth"],
            "selection_confidence": "High",
        }

        def fake_call(**kw):
            # Ensure caller passed the S1 schema, not the monolithic one.
            assert kw["schema"] is S1_AUDIT_SCHEMA
            payload = json.loads(kw["user"])
            assert "grounding" in payload
            assert payload["grounding"]["ticker"] == "MSFT"
            return canned

        monkeypatch.setattr(s1_audit, "call_json", fake_call)
        res = s1_audit.run(g)

        assert isinstance(res, StageResult)
        assert res.stage == "S1_audit"
        assert res.guard is None
        assert res.output == canned
        assert res.latency_ms >= 0

    def test_log_record_shape(self, monkeypatch, g):
        monkeypatch.setattr(s1_audit, "call_json",
                            lambda **kw: {"missing_inputs": [],
                                          "model_archetype": "Growth",
                                          "archetype_rationale": "x",
                                          "primary_model": "DCF",
                                          "model_rationale": "x",
                                          "required_inputs": [],
                                          "selection_confidence": "High"})
        res = s1_audit.run(g)
        log = res.to_log()
        assert log["stage"] == "S1_audit"
        assert "latency_ms" in log
        assert log["retries"] == 0
        assert "guard" not in log     # S1 emits no numeric guard


# ----------------------------------------------------------- S2 tests ---


def _good_s2_output() -> dict:
    """Intrinsic block whose numbers all come from grounding or derivation."""
    return {
        "missing_inputs": [
            "tax_rate: ASSUMPTION used = 0.21 (US statutory)",
        ],
        "economic_value": {
            "bear": {
                "probability_pct": 25,
                "price": 320.0,
                "fundamental": 320.0,
                "value_decomposition": {
                    "fundamental": 320.0,
                    "regime_adjustment": 0,
                    "market_expectations_adjustment": 0,
                    "optionality": 0,
                    "behavioral_premium": 0,
                },
                "derivation": [
                    "rev_2026 = revenue_ttm * (1 + 0.04) = 254.8",
                    "fcf_2026 = rev_2026 * 0.28 = 71.3",
                    "fair_value_bear = fcf_2026 / shares_out * multiple = 320",
                ],
                "conditions": ["Cloud growth slows to 6%"],
                "rationale": "Bear case assumes margin compression.",
            },
            "base": {
                "probability_pct": 50,
                "price": 470.0,
                "fundamental": 470.0,
                "value_decomposition": {
                    "fundamental": 470.0,
                    "regime_adjustment": 0,
                    "market_expectations_adjustment": 0,
                    "optionality": 0,
                    "behavioral_premium": 0,
                },
                "derivation": [
                    "rev_2026 = revenue_ttm * (1 + 0.12) = 274.4",
                    "fcf_2026 = rev_2026 * 0.30 = 82.3",
                    "fair_value_base = fcf_2026 / shares_out * multiple = 470",
                ],
                "conditions": ["Cloud growth holds at 12%"],
                "rationale": "Base case continues current trajectory.",
            },
            "bull": {
                "probability_pct": 25,
                "price": 600.0,
                "fundamental": 600.0,
                "value_decomposition": {
                    "fundamental": 600.0,
                    "regime_adjustment": 0,
                    "market_expectations_adjustment": 0,
                    "optionality": 0,
                    "behavioral_premium": 0,
                },
                "derivation": [
                    "rev_2026 = revenue_ttm * (1 + 0.20) = 294.0",
                    "fcf_2026 = rev_2026 * 0.34 = 100.0",
                    "fair_value_bull = fcf_2026 / shares_out * multiple = 600",
                ],
                "conditions": ["AI monetisation accelerates"],
                "rationale": "Bull case assumes Copilot ramp.",
            },
            "central_estimate": 465.0,
            "low_range": 320.0,
            "high_range": 600.0,
            "key_drivers": ["Cloud growth", "Operating margin", "Buybacks"],
            "key_sensitivities": ["Long-term growth rate", "Discount rate"],
        },
    }


class TestS2Intrinsic:
    def test_calls_with_trimmed_payload_and_s1_context(self, monkeypatch, g):
        s1_out = {
            "model_archetype": "Growth",
            "archetype_rationale": "rationale",
            "primary_model": "DCF",
            "model_rationale": "rationale",
            "required_inputs": ["revenue_ttm", "free_cash_flow", "shares_out"],
            "missing_inputs": ["tax_rate: ASSUMPTION used = 0.21 (US)"],
            "selection_confidence": "High",
        }

        captured: dict = {}

        def fake_call(**kw):
            assert kw["schema"] is S2_INTRINSIC_SCHEMA
            captured["user"] = json.loads(kw["user"])
            return _good_s2_output()

        monkeypatch.setattr(s2_intrinsic, "call_json", fake_call)
        res = s2_intrinsic.run(g, s1_out)

        assert res.stage == "S2_intrinsic"
        assert res.guard is not None
        assert res.guard.passed, res.guard.unjustified

        # Payload to LLM must include the fundamental subset + the S1 context.
        gf = captured["user"]["grounding_fundamentals"]
        assert gf["revenue_ttm"] == g.revenue_ttm
        assert gf["free_cash_flow"] == g.free_cash_flow
        # Behavior fields must NOT be in S2's payload.
        assert "rsi_14" not in gf
        assert "sma_50" not in gf
        assert "implied_vol_30d" not in gf
        # Investor parameters must NOT be smuggled in.
        assert "investor_parameters" not in gf

        s1_ctx = captured["user"]["s1_audit"]
        assert s1_ctx["model_archetype"] == "Growth"
        assert s1_ctx["primary_model"] == "DCF"
        assert s1_ctx["carry_assumptions"] == s1_out["missing_inputs"]

    def test_guard_flags_hallucinated_number(self, monkeypatch, g):
        bad = _good_s2_output()
        # Inject a number into a leaf that has NO derivation entry: the
        # regime_adjustment slot is supposed to be 0 in an intrinsic block.
        # 13.37 is far from every grounding value at every scale factor.
        bad["economic_value"]["base"]["value_decomposition"]["regime_adjustment"] = 13.37

        monkeypatch.setattr(s2_intrinsic, "call_json", lambda **kw: bad)
        res = s2_intrinsic.run(g, {"model_archetype": "Growth",
                                   "primary_model": "DCF"})
        assert not res.guard.passed
        assert any("regime_adjustment" in u.path
                   for u in res.guard.unjustified)

    def test_log_includes_guard_block(self, monkeypatch, g):
        monkeypatch.setattr(s2_intrinsic, "call_json",
                            lambda **kw: _good_s2_output())
        res = s2_intrinsic.run(g, {"model_archetype": "Growth",
                                   "primary_model": "DCF"})
        log = res.to_log()
        assert log["stage"] == "S2_intrinsic"
        assert "guard" in log
        assert log["guard"]["passed"] is True


# ----------------------------------------------------- orchestrator wire ---


class TestOrchestratorSplicing:
    def test_splice_overrides_model_selection_and_economic_value(self):
        report = {
            "model_selection": {
                "primary_archetype": "Cyclical",
                "secondary_archetypes": ["Special situation"],
                "primary_model": "EV/EBITDA",
                "primary_model_rationale": "cyclical-rationale",
                "supporting_models": ["DDM"],
                "excluded_models": [],
                "excluded_reason": "",
                "selection_confidence": "Low",
            },
            "economic_value": {"central_estimate": 1.0},
            "missing_inputs": ["existing"],
        }
        s1 = {
            "model_archetype": "Growth",
            "primary_model": "DCF",
            "model_rationale": "S1 picked DCF",
            "selection_confidence": "High",
            "missing_inputs": ["s1_assumption"],
        }
        s2 = {
            "economic_value": {"central_estimate": 470.0},
            "missing_inputs": ["s2_assumption"],
        }
        spliced = orchestrator._splice_staged(report, s1, s2)

        ms = spliced["model_selection"]
        assert ms["primary_archetype"] == "Growth"
        assert ms["primary_model"] == "DCF"
        assert ms["primary_model_rationale"] == "S1 picked DCF"
        assert ms["selection_confidence"] == "High"
        # Existing secondary/supporting metadata preserved.
        assert ms["secondary_archetypes"] == ["Special situation"]
        assert ms["supporting_models"] == ["DDM"]

        assert spliced["economic_value"]["central_estimate"] == 470.0

        # Missing inputs merged S1 → S2 → existing, de-duplicated.
        assert spliced["missing_inputs"] == [
            "s1_assumption", "s2_assumption", "existing",
        ]

    def test_flag_default_on(self, monkeypatch):
        monkeypatch.delenv("ETV_PIPELINE_STAGED", raising=False)
        assert orchestrator._staged_enabled() is True

    def test_flag_recognised_values(self, monkeypatch):
        for v in ("1", "true", "TRUE", "Yes", "on"):
            monkeypatch.setenv("ETV_PIPELINE_STAGED", v)
            assert orchestrator._staged_enabled() is True
        for v in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("ETV_PIPELINE_STAGED", v)
            assert orchestrator._staged_enabled() is False


# ----------------------------------------------------------- S3 tests ---


def _good_s2_economic_value() -> dict:
    return {
        "bear": {"probability_pct": 20, "fundamental": 350.0, "price": 350.0},
        "base": {"probability_pct": 60, "fundamental": 470.0, "price": 470.0},
        "bull": {"probability_pct": 20, "fundamental": 600.0, "price": 600.0},
    }


def _good_s3_output() -> dict:
    """Overlays whose component values all appear in scenario derivation."""
    def scn(prob, fundamental, regime, mexp, opt, beh):
        price = fundamental + regime + mexp + opt + beh
        return {
            "probability_pct": prob,
            "price": price,
            "fundamental": fundamental,
            "value_decomposition": {
                "fundamental": fundamental,
                "regime_adjustment": regime,
                "market_expectations_adjustment": mexp,
                "optionality": opt,
                "behavioral_premium": beh,
            },
            "regime_multiplier": "1.05x late-cycle / AI capex",
            "behavior_impact": "mild positive institutional inflow",
            "conditions": ["Cloud growth holds"],
            "rationale": "Layered overlays for this scenario.",
            "derivation": [
                f"regime_adjustment = fundamental * factor = {regime}",
                f"market_expectations_adjustment = gap * weight = {mexp}",
                f"optionality = ai_score * fundamental * 0.05 = {opt}",
                f"behavioral_premium = sentiment_score * fundamental * 0.02 = {beh}",
            ],
        }

    return {
        "missing_inputs": [],
        "regime": {
            "primary_regime": "Late-cycle, high-liquidity, AI-driven",
            "secondary_regimes": ["Macro deceleration risk"],
            "confidence": "Medium",
            "macro_drivers": ["Real rates plateau", "AI capex cycle"],
            "model_validity": "partially valid",
            "multiple_bias": "neutral",
            "momentum_durability": "Medium",
            "transition_probability_pct": 30,
            "transition_trigger": "Macro slowdown",
        },
        "optionality": {
            "structural_score_out_of_10": 8,
            "dominant_advantages": ["Scale", "AI distribution"],
            "low_realisation": 0.0,
            "base_realisation": 25.0,
            "high_realisation": 80.0,
            "probability_weighted": 27.0,
            "strategic_scarcity": "High",
            "pathways": ["Copilot ramp"],
            "decay_risks": ["Regulatory headwinds"],
        },
        "market_implied": {
            "implied_revenue_growth_pct": 13,
            "implied_margin_pct": 46,
            "implied_growth_duration_years": 8,
            "implied_tam_capture_pct": 18,
            "expectation_gaps": ["AI overestimated near-term"],
            "overall_assessment": "Priced to perfection",
        },
        "market_behavior": {
            "sentiment": "Positive",
            "narrative_intensity": "High",
            "institutional_flow": "Net buying",
            "crowding_risk": "Medium",
            "momentum": "Weak uptrend",
            "options_positioning": "Skew flat",
            "behavioral_edge": "Marginal",
            "key_risks": ["Crowded long"],
        },
        "etv": {
            "bear": scn(20, 350.0, -10, -5, 5, -5),
            "base": scn(60, 470.0, 5, -10, 25, 10),
            "bull": scn(20, 600.0, 30, 20, 80, 30),
            "probability_weighted_etv": 500.0,
            "current_price": 420.0,
            "expected_return_pct": 19.0,
            "distribution_skew": "right-skewed",
            "primary_driver": "AI optionality",
        },
    }


class TestS3Overlays:
    def test_payload_inherits_s2_intrinsic(self, monkeypatch, g):
        s1_out = {
            "model_archetype": "Mature cash flow",
            "primary_model": "DCF",
            "selection_confidence": "High",
        }
        s2_out = {"economic_value": _good_s2_economic_value()}

        captured: dict = {}

        def fake_call(**kw):
            assert kw["schema"] is S3_OVERLAYS_SCHEMA
            captured["user"] = json.loads(kw["user"])
            return _good_s3_output()

        monkeypatch.setattr(s3_overlays, "call_json", fake_call)
        res = s3_overlays.run(g, s1_out, s2_out, "medium", "moderate")

        assert res.stage == "S3_overlays"
        assert res.guard is not None
        assert res.guard.passed, res.guard.unjustified

        user = captured["user"]
        # Full grounding flows through (overlays need behavior + macro).
        assert user["grounding"]["rsi_14"] == g.rsi_14
        assert user["grounding"]["sma_50"] == g.sma_50
        # Investor parameters explicit.
        assert user["investor_parameters"] == {
            "horizon_preference": "medium",
            "risk_tolerance": "moderate",
        }
        # S1 archetype / model carried forward.
        assert user["s1_audit"]["model_archetype"] == "Mature cash flow"
        # S2 scenarios trimmed to probability + fundamental.
        for s in ("bear", "base", "bull"):
            assert set(user["s2_intrinsic"][s].keys()) == {
                "probability_pct", "fundamental",
            }

    def test_guard_flags_overlay_without_derivation(self, monkeypatch, g):
        bad = _good_s3_output()
        # Inject an unjustified overlay value in base scenario: change the
        # regime_adjustment to a number with no matching derivation line.
        bad["etv"]["base"]["value_decomposition"]["regime_adjustment"] = 27.3
        bad["etv"]["base"]["price"] = (
            bad["etv"]["base"]["fundamental"]
            + 27.3
            + bad["etv"]["base"]["value_decomposition"]["market_expectations_adjustment"]
            + bad["etv"]["base"]["value_decomposition"]["optionality"]
            + bad["etv"]["base"]["value_decomposition"]["behavioral_premium"]
        )

        monkeypatch.setattr(s3_overlays, "call_json", lambda **kw: bad)
        res = s3_overlays.run(g, {"model_archetype": "Growth",
                                  "primary_model": "DCF"},
                              {"economic_value": _good_s2_economic_value()},
                              "medium", "moderate")
        assert not res.guard.passed
        assert any("regime_adjustment" in u.path for u in res.guard.unjustified)

    def test_log_includes_guard_block(self, monkeypatch, g):
        monkeypatch.setattr(s3_overlays, "call_json",
                            lambda **kw: _good_s3_output())
        res = s3_overlays.run(g, {"model_archetype": "Growth",
                                  "primary_model": "DCF"},
                              {"economic_value": _good_s2_economic_value()},
                              "medium", "moderate")
        log = res.to_log()
        assert log["stage"] == "S3_overlays"
        assert "guard" in log
        assert log["guard"]["passed"] is True


# --------------------------------------------- orchestrator splice w/ S3 ---


class TestOrchestratorSplicingWithS3:
    def test_s3_replaces_regime_optionality_marketimplied_behavior_etv(self):
        report = {
            "model_selection": {
                "primary_archetype": "Cyclical",
                "secondary_archetypes": [],
                "primary_model": "EV/EBITDA",
                "primary_model_rationale": "mono",
                "supporting_models": [],
                "excluded_models": [],
                "excluded_reason": "",
                "selection_confidence": "Low",
            },
            "economic_value": {"central_estimate": 1.0},
            "regime": {"primary_regime": "OLD"},
            "optionality": {"structural_score_out_of_10": 0},
            "market_implied": {"overall_assessment": "Fair"},
            "market_behavior": {"sentiment": "Neutral"},
            "etv": {"primary_driver": "OLD"},
            "missing_inputs": ["existing"],
        }
        s1 = {
            "model_archetype": "Growth",
            "primary_model": "DCF",
            "model_rationale": "s1",
            "selection_confidence": "High",
            "missing_inputs": [],
        }
        s2 = {
            "economic_value": {"central_estimate": 470.0},
            "missing_inputs": [],
        }
        s3 = {
            "regime": {"primary_regime": "NEW"},
            "optionality": {"structural_score_out_of_10": 8},
            "market_implied": {"overall_assessment": "Priced to perfection"},
            "market_behavior": {"sentiment": "Positive"},
            "etv": {"primary_driver": "NEW DRIVER"},
            "missing_inputs": ["s3_assumption"],
        }
        spliced = orchestrator._splice_staged(report, s1, s2, s3)
        assert spliced["regime"]["primary_regime"] == "NEW"
        assert spliced["optionality"]["structural_score_out_of_10"] == 8
        assert spliced["market_implied"]["overall_assessment"] == "Priced to perfection"
        assert spliced["market_behavior"]["sentiment"] == "Positive"
        assert spliced["etv"]["primary_driver"] == "NEW DRIVER"
        # Missing inputs include S3.
        assert "s3_assumption" in spliced["missing_inputs"]

    def test_s3_optional_keeps_existing_blocks_when_omitted(self):
        report = {
            "model_selection": {
                "primary_archetype": "X", "secondary_archetypes": [],
                "primary_model": "Y", "primary_model_rationale": "",
                "supporting_models": [], "excluded_models": [],
                "excluded_reason": "", "selection_confidence": "Low",
            },
            "economic_value": {},
            "regime": {"primary_regime": "KEPT"},
            "etv": {"primary_driver": "KEPT"},
            "missing_inputs": [],
        }
        # No s3 argument → backward-compatible 3-arg call.
        spliced = orchestrator._splice_staged(report, {"model_archetype": "Growth",
                                                       "primary_model": "DCF"}, {})
        assert spliced["regime"]["primary_regime"] == "KEPT"
        assert spliced["etv"]["primary_driver"] == "KEPT"


# ----------------------------------------------------------- S4 tests ---


def _good_s4_output() -> dict:
    """Decision / sizing / risk emitted by S4 — all numbers are heuristic."""
    return {
        "missing_inputs": [],
        "risk": {
            "top_risks": [
                {
                    "name": "Cloud growth deceleration",
                    "probability_pct": 30,
                    "magnitude_pct": 18,
                    "expected_cost_pct": 5.4,
                    "trigger": "Azure growth < 20% YoY",
                },
                {
                    "name": "Regulatory action (antitrust / AI)",
                    "probability_pct": 20,
                    "magnitude_pct": 12,
                    "expected_cost_pct": 2.4,
                    "trigger": "Enforcement action announced",
                },
            ],
            "stress_scenario_name": "Late-cycle multiple contraction",
            "stress_etv": 380.0,
            "stress_return_pct": -9.5,
            "stress_probability_pct": 15,
            "mae_low_pct": 8.0,
            "mae_high_pct": 18.0,
            "risk_adjusted_expected_return_pct": 11.2,
            "asymmetry_ratio": 2.4,
        },
        "asymmetry": {
            "upside_pct_weighted": 16.0,
            "downside_pct_weighted": 6.7,
            "ratio": 2.39,
            "edge_sources": ["Structural AI optionality",
                             "Capital allocation discipline"],
            "valid": "Yes",
            "driver": "Optionality realisation",
        },
        "decision": {
            "decision": "TRADE",
            "direction": "LONG",
            "confidence_pct": 72,
            "confidence_deductions": [
                "Crowded positioning (-8)",
                "Partial regime validity (-5)",
                "Stress drawdown risk (-5)",
            ],
            "horizon": "Medium",
            "horizon_rationale": "Optionality requires 12-24 months to realise.",
            "horizon_catalysts": ["Quarterly earnings",
                                  "Copilot ARR disclosure"],
        },
        "sizing": {
            "raw_kelly_pct": 14.0,
            "adjusted_kelly_pct": 5.0,
            "recommended_allocation_pct": 5.0,
            "max_allocation_pct": 7.0,
            "stop_loss_price": 355.0,
            "stop_loss_pct": 15.5,
            "reassessment_trigger": "Azure growth < 20% YoY for 2 consecutive quarters",
            "options_structure": "Calls",
            "options_rationale": "Express right-skewed optionality with capped downside.",
        },
        "catalysts": [
            {"name": "FY26 Q1 earnings",
             "timing": "Oct 2026", "direction": "Mixed"},
            {"name": "Copilot enterprise ARR disclosure",
             "timing": "H2 2026", "direction": "Positive"},
        ],
        "failure_conditions": [
            "Cloud growth < 15% for 2 quarters",
            "AI capex shows zero monetisation",
        ],
        "core_thesis": [
            "Durable cash flows + AI platform optionality",
            "Capital allocation discipline",
            "Defensive moat via Microsoft 365",
        ],
        "advisor_challenges": [
            "Are you over-paying for AI optionality?",
            "Is the regulatory tail being priced?",
        ],
    }


class TestS4Decision:
    def test_payload_contains_s1_s2_s3_context(self, monkeypatch, g):
        s1_out = {"model_archetype": "Mature cash flow",
                  "primary_model": "DCF",
                  "selection_confidence": "High"}
        s2_out = {"economic_value": _good_s2_economic_value()}
        s3_out = _good_s3_output()

        captured: dict = {}

        def fake_call(**kw):
            assert kw["schema"] is S4_DECISION_SCHEMA
            captured["user"] = json.loads(kw["user"])
            return _good_s4_output()

        monkeypatch.setattr(s4_decision, "call_json", fake_call)
        res = s4_decision.run(g, s1_out, s2_out, s3_out, "medium", "moderate")

        assert res.stage == "S4_decision"
        assert res.guard is not None
        assert res.guard.passed, res.guard.unjustified

        user = captured["user"]
        # Grounding is trimmed — must include decision context.
        gd = user["grounding"]
        assert gd["current_price"] == g.current_price
        assert gd["implied_vol_30d"] == g.implied_vol_30d
        assert gd["rsi_14"] == g.rsi_14
        # Trimmed: heavy valuation inputs must NOT be in S4's payload.
        assert "revenue_ttm" not in gd
        assert "free_cash_flow" not in gd
        assert "ev_ebitda" not in gd

        # Investor parameters threaded through.
        assert user["investor_parameters"] == {
            "horizon_preference": "medium",
            "risk_tolerance": "moderate",
        }

        # S3 etv block (with overlays + characterisation) carried forward.
        s3_ctx = user["s3_overlays"]
        assert s3_ctx["regime"]["primary_regime"].startswith("Late-cycle")
        assert s3_ctx["etv"]["bear"]["price"] == 335.0  # 350 - 10 - 5 + 5 - 5
        assert s3_ctx["etv"]["base"]["value_decomposition"]["optionality"] == 25

        # S2 carry-forward stays minimal (prob + fundamental only).
        for s in ("bear", "base", "bull"):
            assert set(user["s2_intrinsic"][s].keys()) == {
                "probability_pct", "fundamental",
            }

    def test_guard_passes_on_heuristic_numbers(self, monkeypatch, g):
        # All Kelly / sizing / asymmetry numbers are passthroughs even
        # though none of them appear in grounding.
        monkeypatch.setattr(s4_decision, "call_json",
                            lambda **kw: _good_s4_output())
        res = s4_decision.run(
            g,
            {"model_archetype": "Growth", "primary_model": "DCF"},
            {"economic_value": _good_s2_economic_value()},
            _good_s3_output(),
            "medium", "moderate",
        )
        assert res.guard.passed
        # Most numbers are passthroughs; none should be flagged.
        assert res.guard.unjustified == []

    def test_log_includes_guard_block(self, monkeypatch, g):
        monkeypatch.setattr(s4_decision, "call_json",
                            lambda **kw: _good_s4_output())
        res = s4_decision.run(
            g,
            {"model_archetype": "Growth", "primary_model": "DCF"},
            {"economic_value": _good_s2_economic_value()},
            _good_s3_output(),
            "medium", "moderate",
        )
        log = res.to_log()
        assert log["stage"] == "S4_decision"
        assert "guard" in log
        assert log["guard"]["passed"] is True


# --------------------------------------------- orchestrator splice w/ S4 ---


class TestOrchestratorSplicingWithS4:
    def test_s4_replaces_decision_sizing_risk_asymmetry_catalysts(self):
        report = {
            "model_selection": {
                "primary_archetype": "X", "secondary_archetypes": [],
                "primary_model": "Y", "primary_model_rationale": "",
                "supporting_models": [], "excluded_models": [],
                "excluded_reason": "", "selection_confidence": "Low",
            },
            "economic_value": {},
            "risk": {"stress_scenario_name": "OLD"},
            "asymmetry": {"ratio": 0.5, "valid": "No"},
            "decision": {"decision": "NO TRADE", "direction": "NEUTRAL",
                         "confidence_pct": 30},
            "sizing": {"recommended_allocation_pct": 0},
            "catalysts": [{"name": "OLD", "timing": "n/a", "direction": "Mixed"}],
            "failure_conditions": ["OLD"],
            "core_thesis": ["OLD"],
            "advisor_challenges": ["OLD"],
            "missing_inputs": [],
        }
        s1 = {"model_archetype": "Growth", "primary_model": "DCF",
              "selection_confidence": "High", "missing_inputs": []}
        s2 = {"economic_value": {"central_estimate": 470.0},
              "missing_inputs": []}
        s4 = _good_s4_output()
        s4["missing_inputs"] = ["s4_only"]

        spliced = orchestrator._splice_staged(report, s1, s2, None, s4)
        assert spliced["decision"]["decision"] == "TRADE"
        assert spliced["decision"]["direction"] == "LONG"
        assert spliced["asymmetry"]["valid"] == "Yes"
        assert spliced["asymmetry"]["ratio"] == 2.39
        assert spliced["sizing"]["recommended_allocation_pct"] == 5.0
        assert spliced["risk"]["stress_scenario_name"] == "Late-cycle multiple contraction"
        assert spliced["catalysts"][0]["name"] == "FY26 Q1 earnings"
        assert "Durable cash flows + AI platform optionality" in spliced["core_thesis"]
        assert "s4_only" in spliced["missing_inputs"]

    def test_splice_without_s4_keeps_existing_decision(self):
        report = {
            "model_selection": {
                "primary_archetype": "X", "secondary_archetypes": [],
                "primary_model": "Y", "primary_model_rationale": "",
                "supporting_models": [], "excluded_models": [],
                "excluded_reason": "", "selection_confidence": "Low",
            },
            "economic_value": {},
            "decision": {"decision": "KEPT"},
            "sizing": {"recommended_allocation_pct": 99},
            "missing_inputs": [],
        }
        s1 = {"model_archetype": "Growth", "primary_model": "DCF"}
        spliced = orchestrator._splice_staged(report, s1, {})
        assert spliced["decision"]["decision"] == "KEPT"
        assert spliced["sizing"]["recommended_allocation_pct"] == 99


# ----------------------------------------------------------- S5 tests ---


def _good_s5_pass() -> dict:
    return {
        "overall_verdict": "pass",
        "stage_verdicts": [
            {"stage": "S2", "verdict": "pass",
             "concerns": [], "retry_focus": ""},
            {"stage": "S3", "verdict": "pass",
             "concerns": [], "retry_focus": ""},
            {"stage": "S4", "verdict": "pass",
             "concerns": [], "retry_focus": ""},
        ],
        "summary": "All four stages internally consistent.",
    }


def _good_s5_retry_s3() -> dict:
    return {
        "overall_verdict": "retry",
        "stage_verdicts": [
            {"stage": "S2", "verdict": "pass",
             "concerns": [], "retry_focus": ""},
            {"stage": "S3", "verdict": "retry",
             "concerns": [
                 "etv.base.price != Σ(value_decomposition) (Δ=$8)",
                 "behavioral_premium magnitude > 25% of fundamental",
             ],
             "retry_focus": "Recompute base price = Σ decomposition; "
                            "cap behavioral_premium at ±10% of fundamental."},
            {"stage": "S4", "verdict": "pass",
             "concerns": [], "retry_focus": ""},
        ],
        "summary": "S3 has a price-decomposition mismatch; recommend retry.",
    }


class TestS5Critic:
    def test_payload_includes_all_stages_and_guards(self, monkeypatch, g):
        captured: dict = {}

        def fake_call(**kw):
            assert kw["schema"] is S5_CRITIC_SCHEMA
            captured["user"] = json.loads(kw["user"])
            return _good_s5_pass()

        monkeypatch.setattr(s5_critic, "call_json", fake_call)
        res = s5_critic.run(
            g,
            {"model_archetype": "Growth"},
            _good_s2_output(),
            _good_s3_output(),
            _good_s4_output(),
        )
        assert res.stage == "S5_critic"
        assert res.guard is None  # critic has no numeric guard of its own.
        user = captured["user"]
        assert user["ticker"] == g.ticker
        assert "s1_audit" in user and "s2_intrinsic" in user
        assert "s3_overlays" in user and "s4_decision" in user
        assert set(user["guards"].keys()) == {"S2", "S3", "S4"}

    def test_pass_verdict_yields_no_retry_stage(self, monkeypatch, g):
        monkeypatch.setattr(s5_critic, "call_json",
                            lambda **kw: _good_s5_pass())
        res = s5_critic.run(
            g, {}, _good_s2_output(), _good_s3_output(), _good_s4_output(),
        )
        assert res.extra.get("overall_verdict") == "pass"
        assert "retry_stage" not in res.extra

    def test_retry_verdict_surfaces_retry_stage_and_focus(self, monkeypatch, g):
        monkeypatch.setattr(s5_critic, "call_json",
                            lambda **kw: _good_s5_retry_s3())
        res = s5_critic.run(
            g, {}, _good_s2_output(), _good_s3_output(), _good_s4_output(),
        )
        assert res.extra["retry_stage"] == "S3"
        assert "decomposition" in res.extra["retry_focus"]
        assert len(res.extra["retry_concerns"]) == 2

    def test_format_feedback_builds_retry_prompt(self):
        text = s5_critic.format_feedback(_good_s5_retry_s3(), "S3")
        assert "CRITIC FEEDBACK" in text
        assert "Stage: S3" in text
        assert "Recompute base price" in text
        assert "behavioral_premium" in text


# --------------------------------------------- stage retry plumbing ---


class TestStageRetryPlumbing:
    """Each stage must accept ``critic_feedback`` and include it in payload."""

    def test_s2_passes_feedback_into_user_payload(self, monkeypatch, g):
        captured: dict = {}

        def fake_call(**kw):
            captured["user"] = json.loads(kw["user"])
            return _good_s2_output()

        monkeypatch.setattr(s2_intrinsic, "call_json", fake_call)
        s2_intrinsic.run(g, {"model_archetype": "Growth",
                              "primary_model": "DCF"},
                         critic_feedback="FIX_ME_PLEASE")
        assert captured["user"]["critic_feedback"] == "FIX_ME_PLEASE"

    def test_s3_passes_feedback_into_user_payload(self, monkeypatch, g):
        captured: dict = {}

        def fake_call(**kw):
            captured["user"] = json.loads(kw["user"])
            return _good_s3_output()

        monkeypatch.setattr(s3_overlays, "call_json", fake_call)
        s3_overlays.run(
            g,
            {"model_archetype": "Growth", "primary_model": "DCF"},
            {"economic_value": _good_s2_economic_value()},
            "medium", "moderate",
            critic_feedback="FIX_S3",
        )
        assert captured["user"]["critic_feedback"] == "FIX_S3"

    def test_s4_passes_feedback_into_user_payload(self, monkeypatch, g):
        captured: dict = {}

        def fake_call(**kw):
            captured["user"] = json.loads(kw["user"])
            return _good_s4_output()

        monkeypatch.setattr(s4_decision, "call_json", fake_call)
        s4_decision.run(
            g,
            {"model_archetype": "Growth", "primary_model": "DCF"},
            {"economic_value": _good_s2_economic_value()},
            _good_s3_output(),
            "medium", "moderate",
            critic_feedback="FIX_S4",
        )
        assert captured["user"]["critic_feedback"] == "FIX_S4"

    def test_no_feedback_means_no_critic_feedback_key(self, monkeypatch, g):
        captured: dict = {}

        def fake_call(**kw):
            captured["user"] = json.loads(kw["user"])
            return _good_s2_output()

        monkeypatch.setattr(s2_intrinsic, "call_json", fake_call)
        s2_intrinsic.run(g, {"model_archetype": "Growth",
                              "primary_model": "DCF"})
        assert "critic_feedback" not in captured["user"]
