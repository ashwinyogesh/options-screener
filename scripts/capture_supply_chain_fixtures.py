"""
Regenerate `expected.json` for every supply-chain characterization fixture.

This is the ONLY supported way to refresh the supply-chain baselines. The
`test_supply_chain_baseline.py` test is assert-only and fails loudly if an
`expected.json` is missing.

Usage (from repo root):

    backend\\venv\\Scripts\\python.exe scripts\\capture_supply_chain_fixtures.py

Each fixture under `backend/tests/fixtures/supply_chain/` is iterated in lexical
order. For every ticker we:

1. Load `inputs.json`,
2. Patch every external boundary in `services.supply_chain_service` via the
   shared `_mocks.patched_supply_chain` helper,
3. Call `scs.get_supply_chain(...)` with the requested `enrich_industry` flag,
4. Sanity-check the resulting graph (company name, accession, enrichment_used
   contract),
5. Write the dataclass to `expected.json` (UTF-8, indent=2, trailing newline).
"""
# ruff: noqa: I001, E402
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
_FIXTURE_ROOT = _BACKEND_ROOT / "tests" / "fixtures" / "supply_chain"

# Make `backend/` importable as the package root and the fixtures dir
# importable so `_mocks.patched_supply_chain` resolves.
for p in (_BACKEND_ROOT, _FIXTURE_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import services.supply_chain_service as scs

from _mocks import patched_supply_chain


def _discover_fixtures() -> list[Path]:
    return sorted(
        d for d in _FIXTURE_ROOT.iterdir()
        if d.is_dir() and (d / "inputs.json").exists()
    )


def _sanity_check(ticker: str, inputs: dict, graph_dict: dict) -> None:
    expected_name = inputs["latest_10k"]["company_name"]
    expected_accession = inputs["latest_10k"]["accession"]
    if graph_dict["company_name"] != expected_name:
        raise AssertionError(
            f"[{ticker}] company_name mismatch: {graph_dict['company_name']!r} != {expected_name!r}"
        )
    if graph_dict["accession"] != expected_accession:
        raise AssertionError(
            f"[{ticker}] accession mismatch: {graph_dict['accession']!r} != {expected_accession!r}"
        )
    enrichment = graph_dict.get("enrichment_used") or []
    if "filing" not in enrichment:
        raise AssertionError(
            f"[{ticker}] enrichment_used missing 'filing': {enrichment!r}"
        )


def _capture_one(fixture_dir: Path) -> dict:
    ticker = fixture_dir.name
    with (fixture_dir / "inputs.json").open("r", encoding="utf-8") as fh:
        inputs = json.load(fh)

    with patched_supply_chain(inputs):
        graph = scs.get_supply_chain(
            inputs["ticker"], enrich_industry=inputs["enrich_industry"]
        )
    graph_dict = asdict(graph)
    _sanity_check(ticker, inputs, graph_dict)

    expected_path = fixture_dir / "expected.json"
    with expected_path.open("w", encoding="utf-8") as fh:
        json.dump(graph_dict, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return graph_dict


def main() -> int:
    fixtures = _discover_fixtures()
    if not fixtures:
        print("No fixtures with inputs.json found", file=sys.stderr)
        return 1
    for fixture_dir in fixtures:
        graph = _capture_one(fixture_dir)
        print(
            f"[ok] {fixture_dir.name}: "
            f"suppliers={len(graph['suppliers'])} "
            f"customers={len(graph['customers'])} "
            f"competitors={len(graph['competitors'])} "
            f"enrichment={graph['enrichment_used']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
