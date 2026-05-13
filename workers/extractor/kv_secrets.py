"""Fetch extractor secrets from Azure Key Vault using managed identity."""
from __future__ import annotations

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


@dataclass(frozen=True)
class ExtractorSecrets:
    openai_api_key: str
    openai_endpoint: str
    openai_deployment: str


def fetch_secrets(keyvault_uri: str) -> ExtractorSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)
    return ExtractorSecrets(
        openai_api_key=client.get_secret("openai-api-key").value or "",
        openai_endpoint=client.get_secret("openai-endpoint").value or "",
        openai_deployment=(
            _try_get(client, "openai-deployment") or "gpt-4o-mini"
        ),
    )


def _try_get(client: SecretClient, name: str) -> str | None:
    try:
        return client.get_secret(name).value
    except Exception:
        return None
