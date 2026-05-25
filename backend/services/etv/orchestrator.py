"""Public entry-point for the ETV service.

Default path is the staged pipeline (S0 scaffold + S1 audit + S2 intrinsic
+ S3 overlays + S4 decision + S5 critic, with at most one critic-driven
retry).  Set ``ETV_PIPELINE_STAGED=0`` to fall back to the original
single-shot monolithic call; the monolithic call is also used as a safety
fallback when any staged step raises.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict

from .grounding import fetch_grounding
from .iv_prior import horizon_to_days
from .llm import AZURE_OPENAI_DEPLOYMENT, Horizon, RiskTolerance, call_monolithic
from .stages import (
    critic_feedback,
    run_s0,
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


def _resolve_s2_reroute(
    s2_output: dict,
    s1_output: dict,
) -> str | None:
    """Return the next ``primary_model`` to retry S2 with, or ``None``.

    Phase 3 of the staged ETV S2 rebuild.  When S2 sets
    ``model_inapplicable=true`` (per v3-final RULE G), the orchestrator
    walks ``s1_output['supporting_models']`` in order and picks the first
    candidate that is *not* the current ``primary_model``.  Returns
    ``None`` when:

    * S2 did not declare the model inapplicable, OR
    * ``supporting_models`` is empty / only contains the current
      primary model.

    Pure function; no mutation, no logging.  The caller is responsible
    for updating ``s1_output['primary_model']``, appending a reroute
    event to ``pipeline_log``, and re-running S2.  Reroute budget is
    capped at one by the call-site (we do not recurse here).
    """
    if not isinstance(s2_output, dict):
        return None
    if s2_output.get("model_inapplicable") is not True:
        return None
    supporting = s1_output.get("supporting_models") or []
    if not isinstance(supporting, list):
        return None
    current = s1_output.get("primary_model")
    for candidate in supporting:
        if isinstance(candidate, str) and candidate and candidate != current:
            return candidate
    return None


def _splice_staged(report: dict, s1: dict, s2: dict,
                   s3: dict | None = None,
                   s4: dict | None = None,
                   *,
                   s0: dict | None = None) -> dict:
    """Overlay staged outputs onto a (possibly empty) report dict.

    Field ownership:
      * **S0** owns ``company_summary`` and the non-primary
        ``model_selection`` metadata (``secondary_archetypes``,
        ``supporting_models``, ``excluded_models``, ``excluded_reason``).
      * **S1** owns ``model_selection.primary_archetype`` /
        ``primary_model`` / ``primary_model_rationale`` /
        ``selection_confidence``.
      * **S2** owns ``economic_value``.
      * **S3** (optional) owns ``regime`` / ``optionality`` /
        ``market_implied`` / ``market_behavior`` / ``etv``.
      * **S4** (optional) owns ``risk`` / ``asymmetry`` / ``decision`` /
        ``sizing`` / ``catalysts`` / ``failure_conditions`` /
        ``core_thesis`` / ``advisor_challenges``.
      * ``missing_inputs`` is the union (de-duplicated,
        S1 → S2 → S3 → S4 → existing).
    """
    # ----- S0 first: populate scaffold so S1 inherits the metadata ------
    if s0:
        if "company_summary" in s0:
            report["company_summary"] = s0["company_summary"]
        existing_ms = report.get("model_selection") or {}
        primary_arch = existing_ms.get("primary_archetype", "")
        # Drop the primary from candidate list if present — S1 will set it.
        candidates = [a for a in (s0.get("candidate_archetypes") or [])
                      if a and a != primary_arch]
        existing_ms.setdefault("secondary_archetypes", candidates)
        existing_ms.setdefault("supporting_models",
                               list(s0.get("supporting_models") or []))
        existing_ms.setdefault("excluded_models",
                               list(s0.get("excluded_models") or []))
        existing_ms.setdefault("excluded_reason",
                               s0.get("excluded_reason", ""))
        report["model_selection"] = existing_ms

    archetype = s1.get("model_archetype")
    model = s1.get("primary_model")

    existing_ms = report.get("model_selection") or {}
    # If S0 ran, drop the primary archetype from `secondary_archetypes` now
    # that S1 has chosen one.
    secondary = [a for a in existing_ms.get("secondary_archetypes", [])
                 if a and a != archetype]
    report["model_selection"] = {
        "primary_archetype": archetype or existing_ms.get("primary_archetype", ""),
        "secondary_archetypes": secondary,
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
            s0_res = run_s0(g)
            pipeline_log.append(s0_res.to_log())
            s1_res = run_s1(g)
            pipeline_log.append(s1_res.to_log())
            s2_res = run_s2(g, s1_res.output)
            pipeline_log.append(s2_res.to_log())

            # ----- Phase 3: at most one S2 reroute on model_inapplicable --
            fallback_model = _resolve_s2_reroute(s2_res.output, s1_res.output)
            if fallback_model is not None:
                original_model = s1_res.output.get("primary_model")
                pipeline_log.append({
                    "stage": "S2_reroute",
                    "from_model": original_model,
                    "to_model": fallback_model,
                    "reason": s2_res.output.get("inapplicability_reason"),
                })
                s1_res.output["primary_model"] = fallback_model
                s2_res = run_s2(g, s1_res.output)
                s2_res.retries = (s2_res.retries or 0) + 1
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

            # Build the report from scratch — no monolithic call needed.
            # S0 + S1..S4 jointly cover every field the validator and the
            # frontend consume.
            report = _splice_staged({}, s1_res.output, s2_res.output,
                                    s3_res.output, s4_res.output,
                                    s0=s0_res.output)
        except Exception as exc:
            logger.warning(
                "staged pipeline failed (%s); falling back to monolithic", exc
            )
            pipeline_log.append({"stage": "fallback",
                                 "reason": str(exc)[:200]})
            report = call_monolithic(g, horizon, risk_tolerance)
    else:
        report = call_monolithic(g, horizon, risk_tolerance)

    report = validate_report(
        report,
        spot=g.current_price,
        iv_annual=g.implied_vol_30d,
        horizon_days=horizon_to_days(horizon),
    )

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
