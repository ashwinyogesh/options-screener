"""S3 — overlay layering + regime / behavior / market-implied characterisation.

Inputs:
  * full grounding (overlays need behavior + macro signals)
  * S1 archetype + model
  * S2 strict-intrinsic bear/base/bull
  * investor horizon + risk tolerance

Outputs (match the corresponding monolithic sub-trees):
  * ``regime``, ``optionality``, ``market_implied``, ``market_behavior``
  * ``etv``: each scenario inherits S2's ``fundamental`` and adds the four
    overlay components in ``value_decomposition``.

Numeric guard:
  Overlay components (regime_adjustment, market_expectations_adjustment,
  optionality, behavioral_premium) MUST appear in each scenario's
  ``derivation[]`` so the guard can match leaf values against derived
  numbers.  Block-level descriptive percentages (implied_* /
  transition_probability_pct / structural_score_out_of_10 / realisations)
  are passthroughs — they are characterisations, not valuation outputs.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

from ..grounding import EtvGrounding
from ..llm import call_json
from ..numeric_guard import format_report_for_prompt, guard
from ..prompts import S3_SYSTEM
from ..schemas import S3_OVERLAYS_SCHEMA
from ._base import StageResult

# Block-level numeric fields that are characterisations rather than
# valuation outputs.  Exempted from the guard so it focuses on the four
# overlay components and scenario prices.
_S3_PASSTHROUGHS: frozenset[str] = frozenset({
    # ETV scenario / block fields (intrinsic carry-forward + recomputed
    # invariants the validator owns).
    "probability_pct", "price", "fundamental",
    "probability_weighted_etv", "current_price", "expected_return_pct",
    # regime block
    "transition_probability_pct",
    # optionality block
    "structural_score_out_of_10",
    "low_realisation", "base_realisation", "high_realisation",
    "probability_weighted",
    # market_implied block
    "implied_revenue_growth_pct", "implied_margin_pct",
    "implied_growth_duration_years", "implied_tam_capture_pct",
})


def _build_user(
    g: EtvGrounding,
    s1_output: dict,
    s2_output: dict,
    horizon: str,
    risk_tolerance: str,
    critic_feedback: str | None = None,
) -> str:
    econ = s2_output.get("economic_value", {})
    payload: dict[str, Any] = {
        "grounding": asdict(g),
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
            # Carry only the scenario primitives S3 needs — keeps
            # context tight and prevents drift on S2 decisions.
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
    }
    if critic_feedback:
        payload["critic_feedback"] = critic_feedback
    return json.dumps(payload, default=str)


def run(
    g: EtvGrounding,
    s1_output: dict,
    s2_output: dict,
    horizon: str,
    risk_tolerance: str,
    critic_feedback: str | None = None,
) -> StageResult:
    """Run the S3 overlays stage (optionally as a critic-driven retry)."""
    t0 = time.perf_counter()
    output = call_json(
        system=S3_SYSTEM,
        user=_build_user(g, s1_output, s2_output, horizon, risk_tolerance,
                         critic_feedback),
        schema=S3_OVERLAYS_SCHEMA,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    guard_report = guard(output, g, extra_passthroughs=_S3_PASSTHROUGHS)

    extra: dict[str, Any] = {}
    if not guard_report.passed:
        extra["guard_summary"] = format_report_for_prompt(guard_report)

    return StageResult(
        stage="S3_overlays",
        output=output,
        guard=guard_report,
        latency_ms=latency_ms,
        extra=extra,
    )


__all__ = ["run"]
