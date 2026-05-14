"""Configuration for the conviction classifier worker.

Env contract (set by Container Apps Job):
    KEYVAULT_URI        e.g. https://kv-narrative-tinkerhub.vault.azure.net/
    COSMOS_ENDPOINT     e.g. https://cosmos-nr-tinkerhub.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    BATCH_SIZE          signals per GPT call batch (default 50)
    MAX_SIGNALS_PER_RUN hard cap per job execution (default 200)

Secret contract (Key Vault):
    openai-api-key          Azure OpenAI key
    openai-endpoint         Azure OpenAI endpoint URL
    openai-deployment       chat deployment name (default gpt-4o-mini)
    embed-deployment        embedding deployment name (default text-embedding-ada-002)
    conviction-prompt-v1    system prompt template (optional, falls back to default)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassifierConfig:
    keyvault_uri: str
    cosmos_endpoint: str
    cosmos_db: str = "narrative"
    log_level: str = "INFO"
    batch_size: int = 50
    max_signals_per_run: int = 200


def load_from_env() -> ClassifierConfig:
    return ClassifierConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        cosmos_db=os.getenv("COSMOS_DB", "narrative"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        batch_size=int(os.getenv("BATCH_SIZE", "50")),
        max_signals_per_run=int(os.getenv("MAX_SIGNALS_PER_RUN", "200")),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
