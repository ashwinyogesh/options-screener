"""Public entry-point for the ETV service.

Default path is the monolithic single-call (Step 1 behavior).  When the
environment flag ``ETV_PIPELINE_STAGED=1`` is set, S1 (audit) and S2
(intrinsic) replace the corresponding sections of the monolithic report.
Remaining sections (overlays, decision, sizing) still come from the
monolithic call until stages S3 / S4 land.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict

from .grounding import fetch_grounding
from .llm import AZURE_OPENAI_DEPLOYMENT, Horizon, RiskTolerance, call_monolithic
from .stages import (
    critic_feedback,
    run_s1,
    run_s2,
    run_s3,
    run_s4,
    run_s5,
)
from .validator import validate_report

__all__ = ["Horizon", "RiskTolerance", "get_etv"]

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SEC = 6 * 60 * 60  # 6h


def _staged_enabled() -> bool:
    return os.getenv("ETV_PIPELINE_STAGED", "1").lower() in {"1", "true", "yes", "on"}


def _splice_staged(report: dict, s1: dict, s2: dict,
                   s3: dict | None = None,
                   s4: dict | None = None) -> dict:
    """Overlay S1 + S2 (+ optional S3 / S4) outputs onto a monolithic report.

    * ``model_selection`` is rebuilt from S1's archetype / model picks.
    * ``economic_value`` is replaced by S2's intrinsic block.
    * When ``s3`` is supplied, ``regime`` / ``optionality`` /
      ``market_implied`` / ``market_behavior`` / ``etv`` are replaced too.
    * When ``s4`` is supplied, ``risk`` / ``asymmetry`` / ``decision`` /
      ``sizing`` / ``catalysts`` / ``failure_conditions`` / ``core_thesis`` /
      ``advisor_challenges`` are replaced too.
    * ``missing_inputs`` is the union (de-duplicated,
      S1 → S2 → S3 → S4 → existing).
    """
    archetype = s1.get("model_archetype")
    model = s1.get("primary_model")

    existing_ms = report.get("model_selection") or {}
    report["model_selection"] = {
        "primary_archetype": archetype or existing_ms.get("primary_archetype", ""),
        "secondary_archetypes": existing_ms.get("secondary_archetypes", []),
        "primary_model": model or existing_ms.get("primary_model", ""),
        "primary_model_rationale": s1.get("model_rationale")
            or existing_ms.get("primary_model_rationale", ""),
        "supporting_models": existing_ms.get("supporting_models", []),
        "excluded_models": existing_ms.get("excluded_models", []),
        "excluded_reason": existing_ms.get("excluded_reason", ""),
        "selection_confidence": s1.get("selection_confidence")
            or existing_ms.get("selection_confidence", "Medium"),
    }

    if "economic_value" in s2:
        report["economic_value"] = s2["economic_value"]

    if s3:
        for key in ("regime", "optionality", "market_implied",
                    "market_behavior", "etv"):
            if key in s3:
                report[key] = s3[key]

    if s4:
        for key in ("risk", "asymmetry", "decision", "sizing",
                    "catalysts", "failure_conditions", "core_thesis",
                    "advisor_challenges"):
            if key in s4:
                report[key] = s4[key]

    seen: set[str] = set()
    merged: list[str] = []
    sources = [s1.get("missing_inputs", []),
               s2.get("missing_inputs", [])]
    if s3:
        sources.append(s3.get("missing_inputs", []))
    if s4:
        sources.append(s4.get("missing_inputs", []))
    sources.append(report.get("missing_inputs", []))
    for src in sources:
        for item in src or []:
            if item not in seen:
                seen.add(item)
                merged.append(item)
    report["missing_inputs"] = merged
    return report


def get_etv(
    ticker: str,
    horizon: Horizon = "medium",
    risk_tolerance: RiskTolerance = "moderate",
    refresh: bool = False,
) -> dict:
    """Compute / fetch cached ETV report. Cache key = (ticker, horizon, risk)."""
    key = f"{ticker.upper()}|{horizon}|{risk_tolerance}"
    now = time.time()
    if not refresh and key in _CACHE:
        ts, payload = _CACHE[key]
        if now - ts < _CACHE_TTL_SEC:
            payload = dict(payload)
            payload["cached"] = True
            payload["cache_age_sec"] = int(now - ts)
            return payload

    g = fetch_grounding(ticker)
    pipeline_log: list[dict] = []
    staged = _staged_enabled()

    if staged:
        try:
            s1_res = run_s1(g)
            pipeline_log.append(s1_res.to_log())
            s2_res = run_s2(g, s1_res.output)
            pipeline_log.append(s2_res.to_log())
            s3_res = run_s3(g, s1_res.output, s2_res.output,
                            horizon, risk_tolerance)
            pipeline_log.append(s3_res.to_log())
            s4_res = run_s4(g, s1_res.output, s2_res.output, s3_res.output,
                            horizon, risk_tolerance)
            pipeline_log.append(s4_res.to_log())

            # ----- S5 critic + (at most) one retry --------------------
            s5_res = run_s5(
                g,
                s1_res.output, s2_res.output, s3_res.output, s4_res.output,
                s2_guard=s2_res.guard, s3_guard=s3_res.guard,
                s4_guard=s4_res.guard,
            )
            pipeline_log.append(s5_res.to_log())

            retry_stage = s5_res.extra.get("retry_stage")
            if retry_stage in ("S2", "S3", "S4"):
                feedback = critic_feedback(s5_res.output, retry_stage)
                if retry_stage == "S2":
                    retry = run_s2(g, s1_res.output, critic_feedback=feedback)
                    retry.retries = 1
                    s2_res = retry
                    # Downstream stages were built on the pre-retry S2 —
                    # they remain as-is (single-retry budget), but we mark
                    # the retry in the log for transparency.
                elif retry_stage == "S3":
                    retry = run_s3(g, s1_res.output, s2_res.output,
                                   horizon, risk_tolerance,
                                   critic_feedback=feedback)
                    retry.retries = 1
                    s3_res = retry
                else:  # S4
                    retry = run_s4(g, s1_res.output, s2_res.output,
                                   s3_res.output, horizon, risk_tolerance,
                                   critic_feedback=feedback)
                    retry.retries = 1
                    s4_res = retry
                pipeline_log.append(retry.to_log())

            report = call_monolithic(g, horizon, risk_tolerance)
            report = _splice_staged(report, s1_res.output, s2_res.output,
                                    s3_res.output, s4_res.output)
        except Exception as exc:
            logger.warning(
                "staged pipeline failed (%s); falling back to monolithic", exc
            )
            pipeline_log.append({"stage": "fallback",
                                 "reason": str(exc)[:200]})
            report = call_monolithic(g, horizon, risk_tolerance)
    else:
        report = call_monolithic(g, horizon, risk_tolerance)

    report = validate_report(report, spot=g.current_price)

    payload = {
        "ticker": g.ticker,
        "horizon": horizon,
        "risk_tolerance": risk_tolerance,
        "grounding": asdict(g),
        "report": report,
        "model": AZURE_OPENAI_DEPLOYMENT,
        "cached": False,
        "cache_age_sec": 0,
        "pipeline_enabled": staged,
        "pipeline_log": pipeline_log,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    _CACHE[key] = (now, payload)
    return payload
