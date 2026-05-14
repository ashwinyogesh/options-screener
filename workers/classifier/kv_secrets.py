"""Fetch secrets from Azure Key Vault for the classifier worker."""
from __future__ import annotations

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from classifier import DEFAULT_SYSTEM_PROMPT


@dataclass(frozen=True)
class ClassifierSecrets:
    openai_api_key: str
    openai_endpoint: str
    openai_deployment: str
    prompt_template: str


def fetch_secrets(keyvault_uri: str) -> ClassifierSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)

    def _get(name: str) -> str:
        return client.get_secret(name).value or ""

    return ClassifierSecrets(
        openai_api_key=_get("openai-api-key"),
        openai_endpoint=_get("openai-endpoint"),
        openai_deployment=_get("openai-deployment"),
        prompt_template=DEFAULT_SYSTEM_PROMPT,
    )
