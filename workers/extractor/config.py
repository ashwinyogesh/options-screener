"""Configuration for the extractor worker.

Env contract (set by Container Apps Job):
- KEYVAULT_URI          e.g. https://kv-narrative-tinkerhub.vault.azure.net/
- EVENT_HUB_NAMESPACE   e.g. evhns-narrative-tinkerhub.servicebus.windows.net
- COSMOS_ENDPOINT       e.g. https://cosmos-nr-tinkerhub.documents.azure.com:443/
- LOG_LEVEL             INFO / DEBUG (default INFO)

Secret contract (Key Vault):
- openai-api-key        Azure OpenAI key
- openai-endpoint       Azure OpenAI endpoint URL
- openai-deployment     deployment name (default gpt-4o-mini)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractorConfig:
    keyvault_uri: str
    event_hub_namespace: str
    cosmos_endpoint: str
    log_level: str
    raw_events_hub: str = "reddit-raw-events"
    cosmos_db: str = "narrative"
    # How many EH events to pull per job run (Container Apps Job = one-shot).
    max_events_per_run: int = 500
    # Max tokens sent to OpenAI per call.
    openai_max_tokens: int = 512


def load_from_env() -> ExtractorConfig:
    return ExtractorConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        event_hub_namespace=_required("EVENT_HUB_NAMESPACE"),
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
