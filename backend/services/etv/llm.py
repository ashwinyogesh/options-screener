"""Azure OpenAI client + monolithic LLM call for ETV.

Step 1 preserves the single-call behavior. Per-stage callers added later
will share the :func:`_get_client` factory but build their own messages
and schemas.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Literal

from openai import AzureOpenAI

from .grounding import EtvGrounding
from .prompts import MONOLITHIC_SYSTEM_PROMPT
from .schemas import MONOLITHIC_RESPONSE_SCHEMA

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------- Config ---
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# Determinism knobs for the ETV pipeline. Pinned here (not per-stage) so every
# S1..S5 call shares the same sampling settings. `seed` is a best-effort hint to
# Azure OpenAI — identical inputs + same seed mostly produce identical outputs,
# but the provider does not guarantee bit-exact reproducibility.
ETV_LLM_TEMPERATURE = float(os.getenv("ETV_LLM_TEMPERATURE", "0.0"))
_raw_seed = os.getenv("ETV_LLM_SEED", "7").strip()
ETV_LLM_SEED: int | None = int(_raw_seed) if _raw_seed else None


Horizon = Literal["short", "medium", "long"]
RiskTolerance = Literal["conservative", "moderate", "aggressive"]


def _get_client() -> AzureOpenAI:
    if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT."
        )
    return AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
        timeout=60.0,
        max_retries=1,
    )


def _build_user_prompt(g: EtvGrounding, horizon: Horizon,
                       risk_tolerance: RiskTolerance) -> str:
    payload = asdict(g)
    payload["investor_parameters"] = {
        "horizon_preference": horizon,
        "risk_tolerance": risk_tolerance,
    }
    return json.dumps(payload, default=str)


def call_json(
    *,
    system: str,
    user: str,
    schema: dict,
    temperature: float | None = None,
    seed: int | None = None,
) -> dict:
    """Generic single-call helper used by per-stage callers.

    Tries strict ``json_schema`` first; on any failure falls back to
    ``json_object`` with a trailing instruction.

    ``temperature`` and ``seed`` default to the module-level pins
    (`ETV_LLM_TEMPERATURE`, `ETV_LLM_SEED`) so the whole pipeline shares one
    determinism profile. Callers may still override per-call if needed.
    """
    if temperature is None:
        temperature = ETV_LLM_TEMPERATURE
    if seed is None:
        seed = ETV_LLM_SEED
    extra: dict = {}
    if seed is not None:
        extra["seed"] = seed
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_schema", "json_schema": schema},
            **extra,
        )
    except Exception as exc:
        logger.warning("json_schema failed (%s); falling back to json_object", exc)
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system",
                 "content": system + "\n\nReturn ONLY a JSON object."},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            **extra,
        )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


def call_monolithic(g: EtvGrounding, horizon: Horizon,
                    risk_tolerance: RiskTolerance) -> dict:
    """Single-shot full-report LLM call (current default path)."""
    user = _build_user_prompt(g, horizon, risk_tolerance)
    return call_json(
        system=MONOLITHIC_SYSTEM_PROMPT,
        user=user,
        schema=MONOLITHIC_RESPONSE_SCHEMA,
    )
