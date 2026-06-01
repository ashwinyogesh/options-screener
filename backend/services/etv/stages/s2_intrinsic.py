"""S2 — strict intrinsic valuation (bear / base / bull, fundamental only).

Inputs: a *fundamental subset* of grounding + S1's archetype decision.
Outputs: ``economic_value`` block matching the monolithic schema, with the
four overlay components zeroed and a ``derivation[]`` per scenario.

The numeric guard runs against the S2 output to flag any number that did
not come from grounding, an explicit assumption, or a derivation line.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

from ..grounding import EtvGrounding
from ..llm import call_json
from ..numeric_guard import format_report_for_prompt, guard
from ..prompts import build_s2_system
from ..schemas import S2_INTRINSIC_SCHEMA
from ._base import StageResult

# Grounding fields relevant to fundamental valuation.  Trimming the payload
# keeps S2's context tight and stops the model from anchoring on regime /
# behavioral fields it has no business using here.
_FUNDAMENTAL_FIELDS: tuple[str, ...] = (
    "ticker", "company_name", "sector", "industry", "business_summary",
    "current_price", "market_cap", "enterprise_value", "shares_out",
    "trailing_pe", "forward_pe", "ev_ebitda", "ev_revenue",
    "price_to_fcf", "price_to_book",
    "historical_pe_p25", "historical_pe_p50", "historical_pe_p75",
    "historical_ev_ebitda_p25", "historical_ev_ebitda_p50",
    "historical_ev_ebitda_p75",
    "revenue_ttm", "revenue_growth_yoy", "gross_margin",
    "ebitda", "ebitda_margin", "operating_income", "operating_margin",
    "net_income", "eps_ttm", "free_cash_flow",
    "total_debt", "net_debt", "cash", "capex", "roic",
    "forward_revenue", "forward_eps", "long_term_growth",
    "analyst_count", "analyst_target_mean",
    "analyst_target_high", "analyst_target_low",
    "as_of",
)


def _fundamental_payload(g: EtvGrounding) -> dict[str, Any]:
    full = asdict(g)
    return {k: full.get(k) for k in _FUNDAMENTAL_FIELDS}


def _build_user(g: EtvGrounding, s1_output: dict,
                critic_feedback: str | None = None) -> str:
    payload: dict[str, Any] = {
        "grounding_fundamentals": _fundamental_payload(g),
        "s1_audit": {
            "model_archetype": s1_output.get("model_archetype"),
            "archetype_rationale": s1_output.get("archetype_rationale"),
            "primary_model": s1_output.get("primary_model"),
            "model_rationale": s1_output.get("model_rationale"),
            "required_inputs": s1_output.get("required_inputs", []),
            "carry_assumptions": s1_output.get("missing_inputs", []),
        },
    }
    if critic_feedback:
        payload["critic_feedback"] = critic_feedback
    return json.dumps(payload, default=str)


def run(g: EtvGrounding, s1_output: dict,
        critic_feedback: str | None = None) -> StageResult:
    """Run the S2 intrinsic stage (optionally as a critic-driven retry).

    The system prompt is composed dynamically from
    :func:`prompts.build_s2_system` so that S1's chosen ``primary_model``
    drives which per-model recipe (DCF, EV/EBITDA, DDM, rNPV, ...) is
    appended to the shared global rules.  Unknown models fall back to a
    generic recipe.
    """
    t0 = time.perf_counter()
    output = call_json(
        system=build_s2_system(s1_output.get("primary_model")),
        user=_build_user(g, s1_output, critic_feedback),
        schema=S2_INTRINSIC_SCHEMA,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    # Numeric guard against fundamental grounding only.  current_price is a
    # passthrough by default; ``central_estimate`` / ``low_range`` /
    # ``high_range`` are derived from scenario prices so we exempt them too.
    # ``validate_structure=True`` enables the v3-final advisory checks
    # (canonical net_debt + equity bridge for EV-based recipes, explicit
    # ``sbc_treatment`` line, final-line discipline, ``[ASSUMED]``-tag
    # counting).  Warnings surface in ``guard_report.structure_warnings``
    # and ``assumption_heavy``; they do not flip ``passed``.
    guard_report = guard(
        output,
        g,
        extra_passthroughs={
            "central_estimate", "low_range", "high_range",
            "price", "fundamental",
        },
        validate_structure=True,
    )
    # Surface the advisory summary alongside failures so the critic and the
    # stage log always see structure warnings / assumption-heavy flags even
    # when the strict numeric guard passes.
    extra: dict[str, Any] = {}
    if (not guard_report.passed
            or guard_report.structure_warnings
            or guard_report.assumption_heavy):
        extra["guard_summary"] = format_report_for_prompt(guard_report)
    return StageResult(
        stage="S2_intrinsic",
        output=output,
        guard=guard_report,
        latency_ms=latency_ms,
        extra=extra,
    )


__all__ = ["run"]
