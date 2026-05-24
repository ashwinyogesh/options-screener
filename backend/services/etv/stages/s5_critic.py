"""S5 — institutional critic + single-retry orchestration helper.

The critic stage examines the outputs of S1-S4 (with the S2/S3 numeric
guard reports surfaced) and emits a structured verdict.  It may flag at
most ONE stage (S2, S3, or S4) for a single retry; the orchestrator then
re-runs that stage with ``critic_feedback`` passed through the user
payload and re-splices the result.

Per the design contract:
  * At most 1 retry per pipeline run.
  * Therefore at most 2 LLM calls per stage total.
  * Critic itself is one LLM call regardless.
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..grounding import EtvGrounding
from ..llm import call_json
from ..prompts import S5_SYSTEM
from ..schemas import S5_CRITIC_SCHEMA
from ._base import StageResult


def _guard_summary(guard) -> dict | None:
    if guard is None:
        return None
    return {
        "passed": guard.passed,
        "total_numbers": guard.total_numbers,
        "grounded": guard.grounded_count,
        "derived": guard.derived_count,
        "passthrough": guard.passthrough_count,
        "unjustified": [
            {"path": u.path, "value": u.value}
            for u in guard.unjustified[:8]
        ],
    }


def _build_user(
    g: EtvGrounding,
    s1_output: dict,
    s2_output: dict,
    s3_output: dict,
    s4_output: dict,
    s2_guard=None,
    s3_guard=None,
    s4_guard=None,
) -> str:
    return json.dumps(
        {
            "ticker": g.ticker,
            "current_price": g.current_price,
            "as_of": g.as_of,
            "s1_audit": s1_output,
            "s2_intrinsic": s2_output,
            "s3_overlays": s3_output,
            "s4_decision": s4_output,
            "guards": {
                "S2": _guard_summary(s2_guard),
                "S3": _guard_summary(s3_guard),
                "S4": _guard_summary(s4_guard),
            },
        },
        default=str,
    )


def run(
    g: EtvGrounding,
    s1_output: dict,
    s2_output: dict,
    s3_output: dict,
    s4_output: dict,
    s2_guard=None,
    s3_guard=None,
    s4_guard=None,
) -> StageResult:
    """Run the S5 critic stage."""
    t0 = time.perf_counter()
    output = call_json(
        system=S5_SYSTEM,
        user=_build_user(g, s1_output, s2_output, s3_output, s4_output,
                        s2_guard, s3_guard, s4_guard),
        schema=S5_CRITIC_SCHEMA,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    extra: dict[str, Any] = {
        "overall_verdict": output.get("overall_verdict"),
    }
    # Surface the retry target (if any) so the orchestrator can read it
    # off the StageResult without re-parsing the LLM output.
    retry_stage: str | None = None
    for sv in output.get("stage_verdicts", []) or []:
        if sv.get("verdict") == "retry" and retry_stage is None:
            retry_stage = sv.get("stage")
            extra["retry_stage"] = retry_stage
            extra["retry_focus"] = sv.get("retry_focus", "")
            extra["retry_concerns"] = sv.get("concerns", [])
    return StageResult(
        stage="S5_critic",
        output=output,
        guard=None,
        latency_ms=latency_ms,
        extra=extra,
    )


def format_feedback(critic_output: dict, stage: str) -> str:
    """Render the critic's verdict for ``stage`` as plain-text feedback."""
    for sv in critic_output.get("stage_verdicts", []) or []:
        if sv.get("stage") == stage:
            concerns = "\n".join(f"- {c}" for c in sv.get("concerns", []))
            focus = sv.get("retry_focus", "")
            return (
                "CRITIC FEEDBACK (retry attempt):\n"
                f"Stage: {stage}\n"
                f"Focus: {focus}\n"
                f"Concerns:\n{concerns}\n"
                "Address each concern in your retry output."
            )
    return ""


__all__ = ["run", "format_feedback"]
