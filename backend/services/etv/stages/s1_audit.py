"""S1 — audit + model-archetype selection.

Inputs: full grounding (no LLM-derived context).
Outputs: list of missing inputs (with explicit ASSUMPTION values), chosen
archetype + primary model, and the required inputs S2 needs.  No valuation
math.  No numeric guard runs here — S1 emits descriptive output only.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict

from ..grounding import EtvGrounding
from ..llm import call_json
from ..prompts import S1_SYSTEM
from ..schemas import S1_AUDIT_SCHEMA
from ._base import StageResult


def _build_user(g: EtvGrounding) -> str:
    return json.dumps({"grounding": asdict(g)}, default=str)


def run(g: EtvGrounding) -> StageResult:
    """Run the S1 audit stage."""
    t0 = time.perf_counter()
    output = call_json(
        system=S1_SYSTEM,
        user=_build_user(g),
        schema=S1_AUDIT_SCHEMA,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return StageResult(
        stage="S1_audit",
        output=output,
        guard=None,
        latency_ms=latency_ms,
    )


__all__ = ["run"]
