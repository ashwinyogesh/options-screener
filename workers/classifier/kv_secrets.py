"""Fetch secrets from Azure Key Vault for the classifier worker."""
from __future__ import annotations

from dataclasses import dataclass

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Default prompt lives in classifier.py (which owns CONVICTION_STATES) so that
# the prompt stays in sync with the state list by proximity.
from classifier import DEFAULT_SYSTEM_PROMPT


@dataclass(frozen=True)
class ClassifierSecrets:
    openai_api_key: str
    openai_endpoint: str
    openai_deployment: str
    embed_deployment: str
    prompt_template: str


def fetch_secrets(keyvault_uri: str) -> ClassifierSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)

    def _get(name: str) -> str:
        return client.get_secret(name).value or ""

    def _get_optional(name: str, default: str) -> str:
        try:
            value = client.get_secret(name).value
            return value if value else default
        except ResourceNotFoundError:
            return default  # secret not yet deployed — expected
        # All other exceptions (CredentialUnavailableError, network) propagate
        # so misconfiguration surfaces immediately rather than silently falling back.

    return ClassifierSecrets(
        openai_api_key=_get("openai-api-key"),
        openai_endpoint=_get("openai-endpoint"),
        openai_deployment=_get_optional("openai-deployment", "gpt-4o-mini"),
        embed_deployment=_get_optional("embed-deployment", "text-embedding-3-small"),
        prompt_template=_get_optional("conviction-prompt-v1", DEFAULT_SYSTEM_PROMPT),
    )
