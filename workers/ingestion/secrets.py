"""Pull narrative-platform secrets from Azure Key Vault using managed identity."""
from __future__ import annotations

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


@dataclass(frozen=True)
class IngestionSecrets:
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str
    reddit_author_salt: str


def fetch_secrets(keyvault_uri: str) -> IngestionSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)
    return IngestionSecrets(
        reddit_client_id=client.get_secret("reddit-client-id").value or "",
        reddit_client_secret=client.get_secret("reddit-client-secret").value or "",
        reddit_user_agent=client.get_secret("reddit-user-agent").value or "",
        reddit_author_salt=client.get_secret("reddit-author-salt").value or "",
    )
