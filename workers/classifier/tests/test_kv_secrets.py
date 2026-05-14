"""Unit tests for kv_secrets.fetch_secrets."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError


def _secret(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _make_get_secret(secrets: dict[str, str | Exception]):
    def _get(name: str):
        v = secrets[name]
        if isinstance(v, Exception):
            raise v
        return _secret(v)

    return _get


def test_fetch_secrets_returns_all_values_when_present() -> None:
    from kv_secrets import fetch_secrets

    mapping = {
        "openai-api-key": "key-123",
        "openai-endpoint": "https://openai.example/",
        "openai-deployment": "gpt-4o-mini",
        "embed-deployment": "text-embedding-ada-002",
        "conviction-prompt-v1": "PROMPT",
    }
    sc = MagicMock()
    sc.get_secret.side_effect = _make_get_secret(mapping)

    with patch("kv_secrets.DefaultAzureCredential"), patch(
        "kv_secrets.SecretClient", return_value=sc
    ):
        result = fetch_secrets("https://kv.example/")

    assert result.openai_api_key == "key-123"
    assert result.openai_endpoint == "https://openai.example/"
    assert result.openai_deployment == "gpt-4o-mini"
    assert result.embed_deployment == "text-embedding-ada-002"
    assert result.prompt_template == "PROMPT"


def test_optional_secrets_fall_back_to_defaults_when_missing() -> None:
    from classifier import DEFAULT_SYSTEM_PROMPT
    from kv_secrets import fetch_secrets

    mapping: dict[str, str | Exception] = {
        "openai-api-key": "key-123",
        "openai-endpoint": "https://openai.example/",
        "openai-deployment": ResourceNotFoundError(),
        "embed-deployment": ResourceNotFoundError(),
        "conviction-prompt-v1": ResourceNotFoundError(),
    }
    sc = MagicMock()
    sc.get_secret.side_effect = _make_get_secret(mapping)

    with patch("kv_secrets.DefaultAzureCredential"), patch(
        "kv_secrets.SecretClient", return_value=sc
    ):
        result = fetch_secrets("https://kv.example/")

    assert result.openai_deployment == "gpt-4o-mini"
    assert result.embed_deployment == "text-embedding-ada-002"
    assert result.prompt_template == DEFAULT_SYSTEM_PROMPT


def test_required_secret_missing_propagates_error() -> None:
    """openai-api-key is required — not wrapped with ResourceNotFoundError fallback."""
    from kv_secrets import fetch_secrets

    mapping: dict[str, str | Exception] = {
        "openai-api-key": ResourceNotFoundError("missing"),
        "openai-endpoint": "https://openai.example/",
        "openai-deployment": "gpt-4o-mini",
        "embed-deployment": "text-embedding-ada-002",
        "conviction-prompt-v1": "P",
    }
    sc = MagicMock()
    sc.get_secret.side_effect = _make_get_secret(mapping)

    with patch("kv_secrets.DefaultAzureCredential"), patch(
        "kv_secrets.SecretClient", return_value=sc
    ):
        with pytest.raises(ResourceNotFoundError):
            fetch_secrets("https://kv.example/")
