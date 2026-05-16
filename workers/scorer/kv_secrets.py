"""Fetch secrets from Azure Key Vault for the ACS scorer worker."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

# Design weights per NARRATIVE_METHODOLOGY.md §5.1.
# Overridden at runtime by KV secret `acs-component-weights` (JSON object).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "A_max": 25.0,
    "B_max": 20.0,
    "C_max": 20.0,
    "D_max": 20.0,
    "E_max": 15.0,  # market confirmation: 6·RS_14d + 5·opt_ratio + 4·13F_change
}


@dataclass(frozen=True)
class ScorerSecrets:
    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))


def fetch_secrets(keyvault_uri: str) -> ScorerSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)

    weights = dict(_DEFAULT_WEIGHTS)
    try:
        raw = client.get_secret("acs-component-weights").value or ""
        if raw:
            overrides = json.loads(raw)
            if isinstance(overrides, dict):
                weights.update({k: float(v) for k, v in overrides.items() if k in weights})
                logger.info("ACS weights overridden from Key Vault: %s", weights)
    except ResourceNotFoundError:
        logger.info("acs-component-weights not in Key Vault — using design defaults")
    # All other exceptions propagate: misconfiguration should surface immediately.

    return ScorerSecrets(weights=weights)
