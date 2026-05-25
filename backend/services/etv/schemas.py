"""JSON schemas for ETV LLM responses.

Step 1 keeps the original full-report schema as :func:`monolithic_schema`.
Later steps add narrow per-stage schemas (e.g. ``s2_intrinsic_schema``)
that each cover only one section.
"""
from __future__ import annotations


def monolithic_schema() -> dict:
    """Build the strict json_schema describing the full ETV report."""
    s = {"type": "string"}
    n = {"type": ["number", "null"]}

    def kv(*fields: tuple[str, dict]) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [f[0] for f in fields],
            "properties": dict(fields),
        }

    arr_s = {"type": "array", "items": {"type": "string"}, "minItems": 0, "maxItems": 12}
    value_decomposition = kv(
        ("fundamental", n),
        ("regime_adjustment", n),
        ("market_expectations_adjustment", n),
        ("optionality", n),
        ("behavioral_premium", n),
    )
    scenario = kv(
        ("probability_pct", n),
        ("price", n),
        ("economic_value", n),
        ("optionality_value", n),
        ("regime_multiplier", {"type": ["string", "null"]}),
        ("behavior_impact", {"type": ["string", "null"]}),
        ("value_decomposition", value_decomposition),
        ("conditions", arr_s),
        ("rationale", s),
    )
    risk_row = kv(
        ("name", s),
        ("probability_pct", n),
        ("magnitude_pct", n),
        ("expected_cost_pct", n),
        ("trigger", s),
    )
    sizing = kv(
        ("raw_kelly_pct", n),
        ("adjusted_kelly_pct", n),
        ("recommended_allocation_pct", n),
        ("max_allocation_pct", n),
        ("stop_loss_price", n),
        ("stop_loss_pct", n),
        ("reassessment_trigger", s),
        ("options_structure", {"type": "string",
                               "enum": ["None", "Calls", "Puts", "Put spread",
                                        "Call spread", "Straddle", "Strangle"]}),
        ("options_rationale", s),
    )
    return {
        "name": "etv_report",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "company_summary", "missing_inputs", "model_selection",
                "economic_value", "optionality", "market_implied",
                "market_behavior", "regime", "etv", "risk", "asymmetry",
                "decision", "sizing", "catalysts", "failure_conditions",
                "core_thesis", "advisor_challenges",
            ],
            "properties": {
                "company_summary": s,
                "missing_inputs": arr_s,
                "model_selection": kv(
                    ("primary_archetype", s),
                    ("secondary_archetypes", arr_s),
                    ("primary_model", s),
                    ("primary_model_rationale", s),
                    ("supporting_models", arr_s),
                    ("excluded_models", arr_s),
                    ("excluded_reason", s),
                    ("selection_confidence",
                        {"type": "string", "enum": ["High", "Medium", "Low"]}),
                ),
                "economic_value": kv(
                    ("bear", scenario), ("base", scenario), ("bull", scenario),
                    ("central_estimate", n),
                    ("low_range", n),
                    ("high_range", n),
                    ("key_drivers", arr_s),
                    ("key_sensitivities", arr_s),
                ),
                "optionality": kv(
                    ("structural_score_out_of_10", n),
                    ("dominant_advantages", arr_s),
                    ("low_realisation", n),
                    ("base_realisation", n),
                    ("high_realisation", n),
                    ("probability_weighted", n),
                    ("strategic_scarcity",
                        {"type": "string", "enum": ["High", "Medium", "Low", "None"]}),
                    ("pathways", arr_s),
                    ("decay_risks", arr_s),
                ),
                "market_implied": kv(
                    ("implied_revenue_growth_pct", n),
                    ("implied_margin_pct", n),
                    ("implied_growth_duration_years", n),
                    ("implied_tam_capture_pct", n),
                    ("expectation_gaps", arr_s),
                    ("overall_assessment",
                        {"type": "string",
                         "enum": ["Priced to perfection", "Fair", "Underappreciated"]}),
                ),
                "market_behavior": kv(
                    ("sentiment",
                        {"type": "string",
                         "enum": ["Euphoric", "Positive", "Neutral", "Negative", "Fearful"]}),
                    ("narrative_intensity",
                        {"type": "string", "enum": ["High", "Medium", "Low"]}),
                    ("institutional_flow", s),
                    ("crowding_risk",
                        {"type": "string", "enum": ["High", "Medium", "Low"]}),
                    ("momentum",
                        {"type": "string",
                         "enum": ["Strong uptrend", "Weak uptrend", "Neutral",
                                  "Weak downtrend", "Strong downtrend"]}),
                    ("options_positioning", s),
                    ("behavioral_edge",
                        {"type": "string", "enum": ["Yes", "No", "Marginal"]}),
                    ("key_risks", arr_s),
                ),
                "regime": kv(
                    ("primary_regime", s),
                    ("secondary_regimes", arr_s),
                    ("confidence", {"type": "string", "enum": ["High", "Medium", "Low"]}),
                    ("macro_drivers", arr_s),
                    ("model_validity",
                        {"type": "string",
                         "enum": ["valid", "partially valid", "distorted"]}),
                    ("multiple_bias",
                        {"type": "string",
                         "enum": ["expansion", "neutral", "contraction"]}),
                    ("momentum_durability",
                        {"type": "string", "enum": ["High", "Medium", "Low"]}),
                    ("transition_probability_pct", n),
                    ("transition_trigger", s),
                ),
                "etv": kv(
                    ("bear", scenario), ("base", scenario), ("bull", scenario),
                    ("probability_weighted_etv", n),
                    ("current_price", n),
                    ("expected_return_pct", n),
                    ("distribution_skew",
                        {"type": "string",
                         "enum": ["right-skewed", "symmetric", "left-skewed"]}),
                    ("primary_driver", s),
                ),
                "risk": kv(
                    ("top_risks", {"type": "array", "items": risk_row,
                                   "minItems": 1, "maxItems": 5}),
                    ("stress_scenario_name", s),
                    ("stress_etv", n),
                    ("stress_return_pct", n),
                    ("stress_probability_pct", n),
                    ("mae_low_pct", n),
                    ("mae_high_pct", n),
                    ("risk_adjusted_expected_return_pct", n),
                    ("asymmetry_ratio", n),
                ),
                "asymmetry": kv(
                    ("upside_pct_weighted", n),
                    ("downside_pct_weighted", n),
                    ("ratio", n),
                    ("edge_sources", arr_s),
                    ("valid",
                        {"type": "string", "enum": ["Yes", "No", "Marginal"]}),
                    ("driver", s),
                ),
                "decision": kv(
                    ("decision", {"type": "string", "enum": ["TRADE", "NO TRADE"]}),
                    ("direction",
                        {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]}),
                    ("confidence_pct", n),
                    ("confidence_deductions", arr_s),
                    ("horizon",
                        {"type": "string", "enum": ["Short", "Medium", "Long"]}),
                    ("horizon_rationale", s),
                    ("horizon_catalysts", arr_s),
                ),
                "sizing": sizing,
                "catalysts": {
                    "type": "array",
                    "items": kv(
                        ("name", s),
                        ("timing", s),
                        ("direction",
                            {"type": "string",
                             "enum": ["Positive", "Negative", "Mixed"]}),
                    ),
                    "minItems": 1, "maxItems": 6,
                },
                "failure_conditions": arr_s,
                "core_thesis": arr_s,
                "advisor_challenges": arr_s,
            },
        },
    }


MONOLITHIC_RESPONSE_SCHEMA = monolithic_schema()


# ============================================================== S0 ===
_ARCHETYPES = (
    "Growth",
    "Mature cash flow",
    "Cyclical",
    "Optionality-driven",
    "Pre-revenue / Concept",
    "Financial",
    "Commodity",
    "Special situation",
)


def s0_scaffold_schema() -> dict:
    """Narrative-only scaffold: company summary + alternative archetypes/models.

    Owns the report fields the staged numeric pipeline (S1-S4) does NOT
    produce.  No prices, no overlays — see :data:`prompts.S0_SYSTEM`.
    """
    s = {"type": "string"}
    arr_s = {"type": "array", "items": {"type": "string"},
             "minItems": 1, "maxItems": 6}
    return {
        "name": "etv_s0_scaffold",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "company_summary", "candidate_archetypes",
                "supporting_models", "excluded_models", "excluded_reason",
            ],
            "properties": {
                "company_summary": s,
                "candidate_archetypes": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(_ARCHETYPES)},
                    "minItems": 2,
                    "maxItems": 4,
                },
                "supporting_models": arr_s,
                "excluded_models": arr_s,
                "excluded_reason": s,
            },
        },
    }


S0_SCAFFOLD_SCHEMA = s0_scaffold_schema()


# ============================================================== S1 ===


def s1_audit_schema() -> dict:
    s = {"type": "string"}
    arr_s = {"type": "array", "items": {"type": "string"},
             "minItems": 0, "maxItems": 24}
    return {
        "name": "etv_s1_audit",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "missing_inputs", "model_archetype", "archetype_rationale",
                "primary_model", "model_rationale", "required_inputs",
                "selection_confidence",
            ],
            "properties": {
                "missing_inputs": arr_s,
                "model_archetype": {"type": "string", "enum": list(_ARCHETYPES)},
                "archetype_rationale": s,
                "primary_model": s,
                "model_rationale": s,
                "required_inputs": arr_s,
                "selection_confidence": {
                    "type": "string", "enum": ["High", "Medium", "Low"],
                },
            },
        },
    }


S1_AUDIT_SCHEMA = s1_audit_schema()


# ============================================================== S2 ===
def s2_intrinsic_schema() -> dict:
    """Narrow schema covering ONLY ``economic_value`` (strict intrinsic).

    Mirrors the corresponding sub-tree of the monolithic schema so the
    orchestrator can splice the result directly into the final report.
    Adds `derivation[]` (short audit lines parseable by ``numeric_guard``).
    """
    s = {"type": "string"}
    n = {"type": ["number", "null"]}
    arr_s = {"type": "array", "items": {"type": "string"},
             "minItems": 0, "maxItems": 12}

    value_decomposition = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "fundamental", "regime_adjustment",
            "market_expectations_adjustment", "optionality",
            "behavioral_premium",
        ],
        "properties": {
            "fundamental": n,
            "regime_adjustment": n,
            "market_expectations_adjustment": n,
            "optionality": n,
            "behavioral_premium": n,
        },
    }
    intrinsic_scenario = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "probability_pct", "price", "fundamental",
            "likelihood_ratio", "lr_rationale",
            "value_decomposition", "derivation", "conditions", "rationale",
        ],
        "properties": {
            "probability_pct": n,
            "price": n,
            "fundamental": n,
            # Bayesian update vs. the lognormal IV prior.  Range
            # ``[0.25, 4.0]`` enforced server-side via clamping.  An LR
            # of 1.0 means "I agree with the market's cone."  Required
            # by strict mode; nullable so the LLM may emit ``null`` when
            # IV is missing from grounding.
            "likelihood_ratio": n,
            "lr_rationale": s,
            "value_decomposition": value_decomposition,
            "derivation": {"type": "array", "items": {"type": "string"},
                           "minItems": 1, "maxItems": 12},
            "conditions": arr_s,
            "rationale": s,
        },
    }
    return {
        "name": "etv_s2_intrinsic",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "missing_inputs", "economic_value",
                "model_inapplicable", "inapplicability_reason",
            ],
            "properties": {
                "missing_inputs": arr_s,
                # Phase 3: when the S1-chosen ``primary_model`` cannot be
                # applied (e.g. EV/EBITDA on negative EBITDA, DDM on
                # g >= cost_of_equity, NAV on asset-light software), the
                # LLM sets ``model_inapplicable=true`` + provides a one-
                # sentence reason.  The orchestrator then reroutes to the
                # next entry in ``supporting_models`` and re-runs S2 once.
                # Required by strict mode but nullable; emit ``null`` for
                # the reason when the model IS applicable.
                "model_inapplicable": {"type": ["boolean", "null"]},
                "inapplicability_reason": {"type": ["string", "null"]},
                "economic_value": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "bear", "base", "bull",
                        "central_estimate", "low_range", "high_range",
                        "key_drivers", "key_sensitivities",
                    ],
                    "properties": {
                        "bear": intrinsic_scenario,
                        "base": intrinsic_scenario,
                        "bull": intrinsic_scenario,
                        "central_estimate": n,
                        "low_range": n,
                        "high_range": n,
                        "key_drivers": arr_s,
                        "key_sensitivities": arr_s,
                    },
                },
            },
        },
    }


S2_INTRINSIC_SCHEMA = s2_intrinsic_schema()


# ============================================================== S3 ===
def s3_overlays_schema() -> dict:
    """Schema for S3: four overlays + characterisation blocks + layered ETV.

    Mirrors the ``regime`` / ``optionality`` / ``market_implied`` /
    ``market_behavior`` / ``etv`` sub-trees of the monolithic schema so the
    orchestrator can splice the result directly into the final report.
    Adds per-scenario `derivation[]` on ETV scenarios.
    """
    s = {"type": "string"}
    n = {"type": ["number", "null"]}
    arr_s = {"type": "array", "items": {"type": "string"},
             "minItems": 0, "maxItems": 12}

    def kv(*fields: tuple[str, dict]) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [f[0] for f in fields],
            "properties": dict(fields),
        }

    value_decomposition = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "fundamental", "regime_adjustment",
            "market_expectations_adjustment", "optionality",
            "behavioral_premium",
        ],
        "properties": {
            "fundamental": n,
            "regime_adjustment": n,
            "market_expectations_adjustment": n,
            "optionality": n,
            "behavioral_premium": n,
        },
    }
    etv_scenario = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "probability_pct", "price", "fundamental",
            "value_decomposition", "regime_multiplier", "behavior_impact",
            "conditions", "rationale", "derivation",
        ],
        "properties": {
            "probability_pct": n,
            "price": n,
            "fundamental": n,
            "value_decomposition": value_decomposition,
            "regime_multiplier": {"type": ["string", "null"]},
            "behavior_impact": {"type": ["string", "null"]},
            "conditions": arr_s,
            "rationale": s,
            "derivation": {"type": "array", "items": {"type": "string"},
                           "minItems": 4, "maxItems": 16},
        },
    }

    regime = kv(
        ("primary_regime", s),
        ("secondary_regimes", arr_s),
        ("confidence", {"type": "string", "enum": ["High", "Medium", "Low"]}),
        ("macro_drivers", arr_s),
        ("model_validity",
            {"type": "string", "enum": ["valid", "partially valid", "distorted"]}),
        ("multiple_bias",
            {"type": "string", "enum": ["expansion", "neutral", "contraction"]}),
        ("momentum_durability",
            {"type": "string", "enum": ["High", "Medium", "Low"]}),
        ("transition_probability_pct", n),
        ("transition_trigger", s),
    )
    optionality = kv(
        ("structural_score_out_of_10", n),
        ("dominant_advantages", arr_s),
        ("low_realisation", n),
        ("base_realisation", n),
        ("high_realisation", n),
        ("probability_weighted", n),
        ("strategic_scarcity",
            {"type": "string", "enum": ["High", "Medium", "Low", "None"]}),
        ("pathways", arr_s),
        ("decay_risks", arr_s),
    )
    market_implied = kv(
        ("implied_revenue_growth_pct", n),
        ("implied_margin_pct", n),
        ("implied_growth_duration_years", n),
        ("implied_tam_capture_pct", n),
        ("expectation_gaps", arr_s),
        ("overall_assessment",
            {"type": "string",
             "enum": ["Priced to perfection", "Fair", "Underappreciated"]}),
    )
    market_behavior = kv(
        ("sentiment",
            {"type": "string",
             "enum": ["Euphoric", "Positive", "Neutral", "Negative", "Fearful"]}),
        ("narrative_intensity",
            {"type": "string", "enum": ["High", "Medium", "Low"]}),
        ("institutional_flow", s),
        ("crowding_risk",
            {"type": "string", "enum": ["High", "Medium", "Low"]}),
        ("momentum",
            {"type": "string",
             "enum": ["Strong uptrend", "Weak uptrend", "Neutral",
                      "Weak downtrend", "Strong downtrend"]}),
        ("options_positioning", s),
        ("behavioral_edge",
            {"type": "string", "enum": ["Yes", "No", "Marginal"]}),
        ("key_risks", arr_s),
    )
    etv = kv(
        ("bear", etv_scenario), ("base", etv_scenario), ("bull", etv_scenario),
        ("probability_weighted_etv", n),
        ("current_price", n),
        ("expected_return_pct", n),
        ("distribution_skew",
            {"type": "string",
             "enum": ["right-skewed", "symmetric", "left-skewed"]}),
        ("primary_driver", s),
    )

    return {
        "name": "etv_s3_overlays",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "missing_inputs", "regime", "optionality",
                "market_implied", "market_behavior", "etv",
            ],
            "properties": {
                "missing_inputs": arr_s,
                "regime": regime,
                "optionality": optionality,
                "market_implied": market_implied,
                "market_behavior": market_behavior,
                "etv": etv,
            },
        },
    }


S3_OVERLAYS_SCHEMA = s3_overlays_schema()


# ============================================================== S4 ===
def s4_decision_schema() -> dict:
    """Strict json_schema for the S4 decision / sizing / risk stage."""
    s = {"type": "string"}
    n = {"type": ["number", "null"]}

    def kv(*fields: tuple[str, dict]) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [f[0] for f in fields],
            "properties": dict(fields),
        }

    arr_s = {"type": "array", "items": {"type": "string"},
             "minItems": 0, "maxItems": 12}

    risk_row = kv(
        ("name", s),
        ("probability_pct", n),
        ("magnitude_pct", n),
        ("expected_cost_pct", n),
        ("trigger", s),
    )

    risk = kv(
        ("top_risks", {"type": "array", "items": risk_row,
                       "minItems": 1, "maxItems": 5}),
        ("stress_scenario_name", s),
        ("stress_etv", n),
        ("stress_return_pct", n),
        ("stress_probability_pct", n),
        ("mae_low_pct", n),
        ("mae_high_pct", n),
        ("risk_adjusted_expected_return_pct", n),
        ("asymmetry_ratio", n),
    )

    asymmetry = kv(
        ("upside_pct_weighted", n),
        ("downside_pct_weighted", n),
        ("ratio", n),
        ("edge_sources", arr_s),
        ("valid", {"type": "string", "enum": ["Yes", "No", "Marginal"]}),
        ("driver", s),
    )

    decision = kv(
        ("decision", {"type": "string", "enum": ["TRADE", "NO TRADE"]}),
        ("direction", {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]}),
        ("confidence_pct", n),
        ("confidence_deductions", arr_s),
        ("horizon", {"type": "string", "enum": ["Short", "Medium", "Long"]}),
        ("horizon_rationale", s),
        ("horizon_catalysts", arr_s),
    )

    sizing = kv(
        ("raw_kelly_pct", n),
        ("adjusted_kelly_pct", n),
        ("recommended_allocation_pct", n),
        ("max_allocation_pct", n),
        ("stop_loss_price", n),
        ("stop_loss_pct", n),
        ("reassessment_trigger", s),
        ("options_structure", {"type": "string",
                               "enum": ["None", "Calls", "Puts", "Put spread",
                                        "Call spread", "Straddle", "Strangle"]}),
        ("options_rationale", s),
    )

    catalysts = {
        "type": "array",
        "items": kv(
            ("name", s),
            ("timing", s),
            ("direction",
                {"type": "string", "enum": ["Positive", "Negative", "Mixed"]}),
        ),
        "minItems": 1, "maxItems": 6,
    }

    return {
        "name": "etv_s4_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "missing_inputs", "risk", "asymmetry", "decision", "sizing",
                "catalysts", "failure_conditions", "core_thesis",
                "advisor_challenges",
            ],
            "properties": {
                "missing_inputs": arr_s,
                "risk": risk,
                "asymmetry": asymmetry,
                "decision": decision,
                "sizing": sizing,
                "catalysts": catalysts,
                "failure_conditions": arr_s,
                "core_thesis": arr_s,
                "advisor_challenges": arr_s,
            },
        },
    }


S4_DECISION_SCHEMA = s4_decision_schema()


# ============================================================== S5 ===
def s5_critic_schema() -> dict:
    """Strict json_schema for the S5 critic stage."""
    s = {"type": "string"}

    def kv(*fields: tuple[str, dict]) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [f[0] for f in fields],
            "properties": dict(fields),
        }

    arr_s = {"type": "array", "items": {"type": "string"},
             "minItems": 0, "maxItems": 8}

    stage_verdict = kv(
        ("stage", {"type": "string", "enum": ["S2", "S3", "S4"]}),
        ("verdict", {"type": "string", "enum": ["pass", "retry"]}),
        ("concerns", arr_s),
        ("retry_focus", s),
    )

    return {
        "name": "etv_s5_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["overall_verdict", "stage_verdicts", "summary"],
            "properties": {
                "overall_verdict": {"type": "string",
                                    "enum": ["pass", "retry"]},
                "stage_verdicts": {"type": "array", "items": stage_verdict,
                                   "minItems": 3, "maxItems": 3},
                "summary": s,
            },
        },
    }


S5_CRITIC_SCHEMA = s5_critic_schema()
