"""
Shared mocking surface for the supply-chain characterization fixtures.

Used by:
- `backend/tests/integration/test_supply_chain_baseline.py`
- `backend/tests/fixtures/supply_chain/_capture.py`

Both consumers feed an `inputs.json` payload through `patched_supply_chain`,
which monkeypatches every external boundary in `services.supply_chain_service`
for the duration of the context. The patches are restored on exit.

NOTE: this helper monkeypatches module-level functions and WILL need to be
rewritten in lockstep with the Phase 1 pipeline refactor (when those functions
move into adapter classes). The companion `inputs.json` / `expected.json`
fixtures, by contrast, are intended to port over to the new pipeline tests
as-is — they encode the orchestrator-level contract.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

import services.supply_chain_service as scs


def _build_mock_funcs(inputs: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    target_ticker = inputs["ticker"].upper()
    cik = inputs["cik"]
    latest_10k = inputs["latest_10k"]
    filing_text = inputs["filing_text"]
    recent_8ks = inputs["recent_8ks"]
    eight_k_texts = inputs["eight_k_texts"]
    filing_pass = inputs["filing_pass_response"]
    industry_pass = inputs.get("industry_pass_response", {})
    verifier_pass = inputs.get("verifier_pass_response", {})
    eight_k_fail_urls = set(inputs.get("eight_k_fail_urls", []))
    verifier_should_fail = bool(inputs.get("verifier_should_fail", False))
    industry_should_fail = bool(inputs.get("industry_should_fail", False))

    def _resolve_cik(ticker: str) -> str | None:
        return cik if ticker.upper() == target_ticker else None

    def _fetch_latest_10k(_cik: str) -> dict | None:
        return latest_10k

    def _fetch_filing_text(_url: str) -> str:
        return filing_text

    def _fetch_recent_8ks(_cik: str, since_date: str, max_count: int = 8) -> list[dict]:
        return list(recent_8ks)

    def _fetch_8k_text(url: str, max_chars: int = 30_000) -> str:
        if url in eight_k_fail_urls:
            raise RuntimeError("simulated 8-K fetch failure")
        return eight_k_texts.get(url, "")

    def _call_llm(*_args: Any, **_kwargs: Any) -> dict:
        return filing_pass

    def _call_industry_llm(*_args: Any, **_kwargs: Any) -> dict:
        if industry_should_fail:
            raise RuntimeError("simulated industry LLM failure")
        return industry_pass

    def _call_verifier_llm(*_args: Any, **_kwargs: Any) -> dict:
        if verifier_should_fail:
            raise RuntimeError("simulated verifier LLM failure")
        return verifier_pass

    return {
        "resolve_cik": _resolve_cik,
        "_fetch_latest_10k": _fetch_latest_10k,
        "_fetch_filing_text": _fetch_filing_text,
        "_fetch_recent_8ks": _fetch_recent_8ks,
        "_fetch_8k_text": _fetch_8k_text,
        "_call_llm": _call_llm,
        "_call_industry_llm": _call_industry_llm,
        "_call_verifier_llm": _call_verifier_llm,
    }


@contextmanager
def patched_supply_chain(inputs: dict[str, Any]) -> Iterator[None]:
    """Patch every external boundary in `supply_chain_service` for `inputs`."""
    funcs = _build_mock_funcs(inputs)
    originals: dict[str, Any] = {}
    try:
        for name, fn in funcs.items():
            originals[name] = getattr(scs, name)
            setattr(scs, name, fn)
        yield
    finally:
        for name, original in originals.items():
            setattr(scs, name, original)
