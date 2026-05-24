"""S4 — decision / sizing / risk / catalysts / core thesis.

Inputs:
  * trimmed grounding (current_price, market_cap, implied_vol_30d,
    short_pct_float, analyst_recommendation, beta, etc.)
  * S1 archetype + model
  * S2 intrinsic prob + fundamental per scenario
  * S3 etv block (probability_pct, price, value_decomposition) + the four
    characterisation blocks (regime, optionality, market_implied,
    market_behavior)
  * investor horizon + risk tolerance

Outputs (replace the corresponding monolithic sub-trees):
  ``risk``, ``asymmetry``, ``decision``, ``sizing``, ``catalysts``,
  ``failure_conditions``, ``core_thesis``, ``advisor_challenges``.

Numeric guard:
  S4 is a JUDGEMENT stage — Kelly, allocation, stop-loss, MAE, and
  initial asymmetry/return numbers are heuristics rather than derived
  valuation outputs (the deterministic validator owns the final
  asymmetry / expected-return / decision-rule recomputation).  All
  numeric leaves emitted by S4 are therefore passthroughs.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

from ..grounding import EtvGrounding
from ..llm import call_json
from ..numeric_guard import format_report_for_prompt, guard
from ..prompts import S4_SYSTEM
from ..schemas import S4_DECISION_SCHEMA
from ._base import StageResult

# Trimmed grounding fields S4 actually needs (decision context, not
# valuation).  Keeps the prompt tight and reduces context drift.
_GROUNDING_FIELDS: tuple[str, ...] = (
    "ticker", "company_name", "sector", "industry",
    "current_price", "market_cap",
    "implied_vol_30d", "rsi_14", "sma_50", "sma_200",
    "short_pct_float", "avg_volume_10d",
    "week52_high", "week52_low",
    "analyst_recommendation", "analyst_target_mean",
    "analyst_target_high", "analyst_target_low",
    "as_of",
)

# S4 emits Kelly / allocation / stop-loss / MAE numbers — these are
# heuristic, not derived from grounding.  The validator recomputes
# asymmetry / expected_return / decision rule deterministically.
_S4_PASSTHROUGHS: frozenset[str] = frozenset({
    # risk block
    "probability_pct", "magnitude_pct", "expected_cost_pct",
    "stress_etv", "stress_return_pct", "stress_probability_pct",
    "mae_low_pct", "mae_high_pct",
    "risk_adjusted_expected_return_pct", "asymmetry_ratio",
    # asymmetry block
    "upside_pct_weighted", "downside_pct_weighted", "ratio",
    # decision block
    "confidence_pct",
    # sizing block
    "raw_kelly_pct", "adjusted_kelly_pct",
    "recommended_allocation_pct", "max_allocation_pct",
    "stop_loss_price", "stop_loss_pct",
})


def _trim_grounding(g: EtvGrounding) -> dict:
    full = asdict(g)
    return {k: full.get(k) for k in _GROUNDING_FIELDS}


def _trim_etv_scenario(scn: dict | None) -> dict:
    scn = scn or {}
    return {
        "probability_pct": scn.get("probability_pct"),
        "price": scn.get("price"),
        "fundamental": scn.get("fundamental"),
        "value_decomposition": scn.get("value_decomposition"),
    }


def _build_user(
    g: EtvGrounding,
    s1_output: dict,
    s2_output: dict,
    s3_output: dict,
    horizon: str,
    risk_tolerance: str,
    critic_feedback: str | None = None,
) -> str:
    econ = s2_output.get("economic_value", {})
    etv = s3_output.get("etv", {})
    payload: dict[str, Any] = {
        "grounding": _trim_grounding(g),
        "investor_parameters": {
            "horizon_preference": horizon,
            "risk_tolerance": risk_tolerance,
        },
        "s1_audit": {
            "model_archetype": s1_output.get("model_archetype"),
            "primary_model": s1_output.get("primary_model"),
            "selection_confidence": s1_output.get("selection_confidence"),
        },
        "s2_intrinsic": {
            "bear": {
                "probability_pct": (econ.get("bear") or {}).get("probability_pct"),
                "fundamental": (econ.get("bear") or {}).get("fundamental"),
            },
            "base": {
                "probability_pct": (econ.get("base") or {}).get("probability_pct"),
                "fundamental": (econ.get("base") or {}).get("fundamental"),
            },
            "bull": {
                "probability_pct": (econ.get("bull") or {}).get("probability_pct"),
                "fundamental": (econ.get("bull") or {}).get("fundamental"),
            },
        },
        "s3_overlays": {
            "regime": s3_output.get("regime"),
            "optionality": s3_output.get("optionality"),
            "market_implied": s3_output.get("market_implied"),
            "market_behavior": s3_output.get("market_behavior"),
            "etv": {
                "bear": _trim_etv_scenario(etv.get("bear")),
                "base": _trim_etv_scenario(etv.get("base")),
                "bull": _trim_etv_scenario(etv.get("bull")),
                "probability_weighted_etv": etv.get("probability_weighted_etv"),
                "current_price": etv.get("current_price"),
                "expected_return_pct": etv.get("expected_return_pct"),
                "distribution_skew": etv.get("distribution_skew"),
                "primary_driver": etv.get("primary_driver"),
            },
        },
    }
    if critic_feedback:
        payload["critic_feedback"] = critic_feedback
    return json.dumps(payload, default=str)


def run(
    g: EtvGrounding,
    s1_output: dict,
    s2_output: dict,
    s3_output: dict,
    horizon: str,
    risk_tolerance: str,
    critic_feedback: str | None = None,
) -> StageResult:
    """Run the S4 decision stage (optionally as a critic-driven retry)."""
    t0 = time.perf_counter()
    output = call_json(
        system=S4_SYSTEM,
        user=_build_user(g, s1_output, s2_output, s3_output,
                         horizon, risk_tolerance, critic_feedback),
        schema=S4_DECISION_SCHEMA,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    guard_report = guard(output, g, extra_passthroughs=_S4_PASSTHROUGHS)

    extra: dict[str, Any] = {}
    if not guard_report.passed:
        extra["guard_summary"] = format_report_for_prompt(guard_report)

    return StageResult(
        stage="S4_decision",
        output=output,
        guard=guard_report,
        latency_ms=latency_ms,
        extra=extra,
    )


__all__ = ["run"]
