"""Shared mocking surface for the supply-chain characterization fixtures.

Used by:
- `backend/tests/integration/test_supply_chain_baseline.py`
- `scripts/capture_supply_chain_fixtures.py`

Both consumers feed an `inputs.json` payload through `patched_supply_chain`,
which monkeypatches the pipeline's default-client / default-extractor
factories to return fakes for the duration of the context. The patches
are restored on exit.

The fakes implement just enough of the `SecDataClient` and
`LlmSupplyChainExtractor` surface that the pipeline calls. The
companion `inputs.json` / `expected.json` fixtures encode the
orchestrator-level contract and are stable across the Phase 1 refactor.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import services.supply_chain.pipeline as pipeline
from services.supply_chain.types import (
    EightKFetchResult,
    LlmFilingResult,
    LlmIndustryResult,
    LlmVerifierResult,
)


class _FakeSecClient:
    """In-memory ``SecDataClient`` for fixture-driven tests."""

    def __init__(self, inputs: dict[str, Any]) -> None:
        self._target_ticker = inputs["ticker"].upper()
        self._cik = inputs["cik"]
        self._latest_10k = inputs["latest_10k"]
        self._filing_text = inputs["filing_text"]
        self._recent_8ks = list(inputs["recent_8ks"])
        self._eight_k_texts = inputs["eight_k_texts"]
        self._eight_k_fail_urls = set(inputs.get("eight_k_fail_urls", []))

    def resolve_cik(self, ticker: str) -> str | None:
        return self._cik if ticker.upper() == self._target_ticker else None

    def get_latest_10k(self, _cik: str) -> dict | None:
        return self._latest_10k

    def fetch_filing_text(self, _url: str) -> str:
        return self._filing_text

    def get_recent_8ks(
        self, _cik: str, since_date: str, max_count: int = 8  # noqa: ARG002
    ) -> list[dict]:
        return list(self._recent_8ks)

    def fetch_8k_text(self, url: str, max_chars: int = 30_000) -> str:  # noqa: ARG002
        if url in self._eight_k_fail_urls:
            raise RuntimeError("simulated 8-K fetch failure")
        return self._eight_k_texts.get(url, "")

    def fetch_8ks_parallel(
        self, items: list[dict], max_workers: int = 4  # noqa: ARG002
    ) -> EightKFetchResult:
        successful: list[tuple[dict, str]] = []
        failed_count = 0
        for meta in items:
            url = meta["primary_doc_url"]
            if url in self._eight_k_fail_urls:
                failed_count += 1
                continue
            successful.append((meta, self._eight_k_texts.get(url, "")))
        return EightKFetchResult(successful=successful, failed_count=failed_count)


class _FakeLlmExtractor:
    """In-memory ``LlmSupplyChainExtractor`` for fixture-driven tests."""

    def __init__(self, inputs: dict[str, Any]) -> None:
        self._filing_pass = LlmFilingResult.model_validate(inputs["filing_pass_response"])
        self._industry_pass = LlmIndustryResult.model_validate(
            inputs.get("industry_pass_response", {})
        )
        self._verifier_pass = LlmVerifierResult.model_validate(
            inputs.get("verifier_pass_response", {})
        )
        self._industry_should_fail = bool(inputs.get("industry_should_fail", False))
        self._verifier_should_fail = bool(inputs.get("verifier_should_fail", False))

    def extract_filing(
        self,
        *,
        ticker: str,
        company_name: str,
        filing_text: str,
        recent_8k_text: str = "",
    ) -> LlmFilingResult:
        del ticker, company_name, filing_text, recent_8k_text
        return self._filing_pass

    def enrich_industry(
        self,
        *,
        ticker: str,
        company_name: str,
        segments: list[str],
        existing: LlmFilingResult,
    ) -> LlmIndustryResult:
        del ticker, company_name, segments, existing
        if self._industry_should_fail:
            raise RuntimeError("simulated industry LLM failure")
        return self._industry_pass

    def verify(
        self,
        *,
        ticker: str,
        company_name: str,
        candidates: LlmIndustryResult,
    ) -> LlmVerifierResult:
        del ticker, company_name, candidates
        if self._verifier_should_fail:
            raise RuntimeError("simulated verifier LLM failure")
        return self._verifier_pass


@contextmanager
def patched_supply_chain(inputs: dict[str, Any]) -> Iterator[None]:
    """Patch the pipeline's default-client/default-extractor factories."""
    fake_sec = _FakeSecClient(inputs)
    fake_llm = _FakeLlmExtractor(inputs)
    original_client = pipeline.get_default_client
    original_extractor = pipeline.get_default_extractor
    try:
        pipeline.get_default_client = lambda: fake_sec  # type: ignore[assignment]
        pipeline.get_default_extractor = lambda: fake_llm  # type: ignore[assignment]
        yield
    finally:
        pipeline.get_default_client = original_client
        pipeline.get_default_extractor = original_extractor
