"""Orchestration tests for the classifier worker `main()`.

Tests cover:
- happy path: classify + embed → write_conviction called with embedding
- soft-fail: embedding error → conviction still written without embedding
- backfill: terminates on empty fetch, short batch, exception, and stall
- exit code: SystemExit(1) when classified==0 and skipped>0
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() reads KEYVAULT_URI and COSMOS_ENDPOINT via config.load_from_env."""
    monkeypatch.setenv("KEYVAULT_URI", "https://kv.example/")
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://cosmos.example/")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BATCH_SIZE", "10")
    monkeypatch.setenv("MAX_SIGNALS_PER_RUN", "10")


@pytest.fixture
def fake_secrets() -> SimpleNamespace:
    return SimpleNamespace(
        openai_api_key="k",
        openai_endpoint="https://openai.example/",
        openai_deployment="gpt-4o-mini",
        embed_deployment="text-embedding-ada-002",
        prompt_template="PROMPT {ticker} {sentiment}",
    )


@pytest.fixture
def patches(fake_secrets):
    """Patch all external boundaries for main(): KV, Cosmos client, OpenAI clients."""
    with patch("main.fetch_secrets", return_value=fake_secrets) as p_secrets, patch(
        "main.CosmosClassifierClient"
    ) as p_cosmos_cls, patch("main.ConvictionClassifier") as p_clf_cls, patch(
        "main.EmbeddingGenerator"
    ) as p_emb_cls:
        cosmos = MagicMock()
        cosmos.fetch_missing_embeddings.return_value = []
        p_cosmos_cls.return_value = cosmos

        clf = MagicMock()
        clf.classify.return_value = ("researched_bull", 0.9)
        p_clf_cls.return_value = clf

        embedder = MagicMock()
        embedder.embed_batch.return_value = [[0.1, 0.2]]
        p_emb_cls.return_value = embedder

        yield SimpleNamespace(
            secrets=p_secrets,
            cosmos=cosmos,
            clf=clf,
            embedder=embedder,
        )


def test_main_happy_path_writes_conviction_with_embedding(patches) -> None:
    from main import main

    patches.cosmos.fetch_unclassified.side_effect = [
        [{"id": "s1", "ticker": "NVDA", "sentiment": "positive", "rationale": "r"}],
        [],
    ]
    patches.embedder.embed_batch.return_value = [[0.1, 0.2, 0.3]]

    main()

    patches.cosmos.write_conviction.assert_called_once()
    kwargs = patches.cosmos.write_conviction.call_args.kwargs
    assert kwargs["embedding"] == [0.1, 0.2, 0.3]
    assert kwargs["embedding_model"] == "text-embedding-ada-002"


def test_main_embedding_failure_still_writes_conviction_without_embedding(patches) -> None:
    from main import main

    patches.cosmos.fetch_unclassified.side_effect = [
        [{"id": "s1", "ticker": "NVDA", "sentiment": "positive", "rationale": "r"}],
        [],
    ]
    patches.embedder.embed_batch.side_effect = RuntimeError("openai down")

    main()

    patches.cosmos.write_conviction.assert_called_once()
    kwargs = patches.cosmos.write_conviction.call_args.kwargs
    assert kwargs["embedding"] is None


def test_main_exits_one_when_all_signals_fail_classification(patches) -> None:
    from main import main

    patches.cosmos.fetch_unclassified.side_effect = [
        [{"id": "s1", "ticker": "NVDA", "sentiment": "positive", "rationale": "r"}],
        [],
    ]
    patches.clf.classify.side_effect = RuntimeError("openai down")

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_main_does_not_exit_when_some_classified(patches) -> None:
    from main import main

    patches.cosmos.fetch_unclassified.side_effect = [
        [
            {"id": "s1", "ticker": "NVDA", "sentiment": "positive", "rationale": "r"},
            {"id": "s2", "ticker": "AAPL", "sentiment": "negative", "rationale": "r"},
        ],
        [],
    ]
    # First succeeds, second raises
    patches.clf.classify.side_effect = [("researched_bull", 0.9), RuntimeError("x")]

    # Should NOT raise SystemExit
    main()


def test_skipped_ids_passed_to_next_fetch(monkeypatch: pytest.MonkeyPatch, patches) -> None:
    """Ids of signals whose classify raised must be excluded on subsequent fetches."""
    # BATCH_SIZE=1 forces a second loop iteration so we can observe skip_ids on the
    # second fetch_unclassified call. (With batch_size > 1 the loop short-circuits
    # via `len(signals) < batch_size`.)
    monkeypatch.setenv("BATCH_SIZE", "1")
    monkeypatch.setenv("MAX_SIGNALS_PER_RUN", "5")
    from main import main

    patches.cosmos.fetch_unclassified.side_effect = [
        [{"id": "bad", "ticker": "X", "sentiment": "neutral", "rationale": "r"}],
        [],
    ]
    patches.clf.classify.side_effect = RuntimeError("x")

    with pytest.raises(SystemExit):
        main()

    # Second fetch_unclassified call gets the skip set including "bad".
    second_call = patches.cosmos.fetch_unclassified.call_args_list[1]
    assert "bad" in second_call.kwargs["skip_ids"]


def test_backfill_runs_when_main_loop_empty(patches) -> None:
    from main import main

    patches.cosmos.fetch_unclassified.return_value = []
    patches.cosmos.fetch_missing_embeddings.side_effect = [
        [
            {"id": "old1", "rationale": "r1", "conviction_state": "researched_bull"},
            {"id": "old2", "rationale": "r2", "conviction_state": "emotional_bull"},
        ],
        [],
    ]
    patches.embedder.embed_batch.return_value = [[0.1], [0.2]]

    main()

    assert patches.cosmos.write_embedding.call_count == 2


def test_backfill_terminates_on_stall(monkeypatch: pytest.MonkeyPatch, patches) -> None:
    """Progress guard: if fetch keeps returning the same ids, break instead of spin."""
    # BATCH_SIZE=1 keeps the backfill loop alive past the first iteration (the
    # `len(docs) < batch_size` short-circuit would otherwise break before the
    # progress guard fires).
    monkeypatch.setenv("BATCH_SIZE", "1")
    from main import main

    patches.cosmos.fetch_unclassified.return_value = []
    stuck_docs = [{"id": "stuck", "rationale": "r"}]
    # Same docs returned forever — simulates a silent write no-op.
    patches.cosmos.fetch_missing_embeddings.return_value = stuck_docs
    patches.embedder.embed_batch.return_value = [[0.1]]

    main()

    # First iteration writes once; second iteration sees only seen-ids and breaks.
    assert patches.cosmos.write_embedding.call_count == 1
    assert patches.cosmos.fetch_missing_embeddings.call_count == 2


def test_backfill_terminates_on_embed_exception(patches) -> None:
    from main import main

    patches.cosmos.fetch_unclassified.return_value = []
    patches.cosmos.fetch_missing_embeddings.return_value = [
        {"id": "old1", "rationale": "r1"}
    ]
    patches.embedder.embed_batch.side_effect = RuntimeError("openai down")

    main()  # should not raise

    patches.cosmos.write_embedding.assert_not_called()
