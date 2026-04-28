"""
Characterization test for `services.supply_chain_service.get_supply_chain`.

Phase 0 safety net for the upcoming supply-chain pipeline refactor. Every
external boundary (SEC HTTP fetches and Azure OpenAI calls) is mocked at the
module-level helper layer; the deterministic orchestrator behaviour is pinned
to the `expected.json` files committed alongside each fixture.

Honest scope notes
------------------
- The mocking surface in `_mocks.py` monkeypatches module-level functions and
  WILL need to be rewritten in lockstep with the Phase 1 pipeline refactor
  (when those functions move into adapter classes).
- The `inputs.json` and `expected.json` files, by contrast, are intended to
  port over to the new pipeline tests as-is — they encode the
  orchestrator-level contract, which is what the refactor is meant to
  preserve.
- Plan reference: `/memories/session/plan.md`.

Branches covered
----------------
Six fixture directories under `backend/tests/fixtures/supply_chain/`:

- `KO_TEST` — single-segment, industry + verifier merge happy path.
- `MSFT_TEST` — multi-segment with two 8-Ks.
- `SMALL_TEST` — `enrich_industry=False`, no 8-Ks.
- `MSFT_8K_FAIL_TEST` — second 8-K fetch raises; loop continues, count still 2.
- `KO_VERIFIER_FAIL_TEST` — verifier LLM raises; falls back to raw industry.
- `KO_INDUSTRY_FAIL_TEST` — industry LLM raises; only filing enrichment.

Regenerating fixtures
---------------------
The test is assert-only. To refresh the `expected.json` files run:

    backend\\venv\\Scripts\\python.exe scripts\\capture_supply_chain_fixtures.py

When to delete: once Phase 1 is finished AND production tests cover the new
pipeline. See `/memories/session/plan-supply-chain-enrichment.md`.
"""
# ruff: noqa: I001
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

import services.supply_chain_service as scs

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "supply_chain"
if str(FIXTURE_ROOT) not in sys.path:
    sys.path.insert(0, str(FIXTURE_ROOT))
from _mocks import patched_supply_chain  # noqa: E402

TICKERS = (
    "KO_TEST",
    "MSFT_TEST",
    "SMALL_TEST",
    "MSFT_8K_FAIL_TEST",
    "KO_VERIFIER_FAIL_TEST",
    "KO_INDUSTRY_FAIL_TEST",
)


def _load_inputs(ticker: str) -> dict:
    path = FIXTURE_ROOT / ticker / "inputs.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.integration
@pytest.mark.parametrize("ticker", TICKERS)
def test_supply_chain_baseline(ticker: str) -> None:
    inputs = _load_inputs(ticker)

    with patched_supply_chain(inputs):
        graph = scs.get_supply_chain(
            inputs["ticker"], enrich_industry=inputs["enrich_industry"]
        )
    actual = asdict(graph)

    expected_path = FIXTURE_ROOT / ticker / "expected.json"
    if not expected_path.exists():
        pytest.fail(
            f"Missing fixture: {expected_path}. Regenerate via "
            "`backend\\venv\\Scripts\\python.exe "
            "scripts\\capture_supply_chain_fixtures.py`."
        )

    with expected_path.open("r", encoding="utf-8") as fh:
        expected = json.load(fh)

    # Per-section asserts first for friendlier diffs.
    assert actual["suppliers"] == expected["suppliers"], "suppliers diverged"
    assert actual["customers"] == expected["customers"], "customers diverged"
    assert actual["competitors"] == expected["competitors"], "competitors diverged"
    # Whole-graph equality catches scalar fields (enrichment_used, eight_k_count, etc.).
    assert actual == expected
