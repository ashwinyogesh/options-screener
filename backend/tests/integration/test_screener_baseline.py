"""
Characterization tests for the three screener services.

These are SAFETY-NET tests, not behavior tests. They lock in the current output
of `csp_service.process_symbol`, `cc_service.process_cc_symbol`, and
`ditm_service.process_symbol` for a curated ticker set so we can refactor those
modules without silent regressions.

Generation:
    python scripts/capture_screener_fixtures.py

What is mocked:
- `services.data_service.get_ohlc` → returns a pickled DataFrame from fixtures.
- `services.options_service.get_all_expirations_data` (CSP)
- `services.options_service.get_all_expirations_calls_data` (CC + DITM)
- DITM macro context is loaded from JSON and passed as a parameter.
- `date.today()` is frozen to the capture date so DTE / earnings windows match.

When to delete:
- Once the screener refactor is complete AND production tests cover the same
  ground (see /memories/session/plan-screener-refactor.md). Until then, these
  must stay green at every phase boundary.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time

# Local fixture helpers from conftest.
from tests.conftest import FIXTURES_ROOT, fixture_path  # type: ignore


SCREENER_ROOT = FIXTURES_ROOT / "screener"
INPUTS_DIR = SCREENER_ROOT / "inputs"
OUTPUTS_DIR = SCREENER_ROOT / "outputs"


# Skip the entire module if fixtures haven't been captured yet.
pytestmark = pytest.mark.skipif(
    not (SCREENER_ROOT / "manifest.json").exists(),
    reason=(
        "Screener fixtures missing. Run `python scripts/capture_screener_fixtures.py` "
        "from the repo root to generate them."
    ),
)


def _manifest() -> dict:
    return json.loads((SCREENER_ROOT / "manifest.json").read_text(encoding="utf-8"))


def _load_input(kind: str, symbol: str, key: str) -> Any:
    path = INPUTS_DIR / f"{kind}__{symbol}__{key}.pkl"
    with path.open("rb") as fh:
        return pickle.load(fh)


def _load_expected(kind: str, symbol: str) -> dict:
    path = OUTPUTS_DIR / f"{kind}__{symbol}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _to_jsonable(obj):
    """Mirror of capture script's serializer — keep in sync."""
    import dataclasses

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    return obj


def _normalize(payload: Any) -> Any:
    """
    Round-trip through JSON with `default=str` so floats / ints / Nones match
    exactly what we wrote to disk in the capture script.
    """
    return json.loads(json.dumps(payload, default=str))


def _ids(symbols: list[str]) -> list[str]:
    return [s.lower() for s in symbols]


# --- CSP --------------------------------------------------------------------

CSP_SYMBOLS = (_manifest()["tickers"]["csp"] if (SCREENER_ROOT / "manifest.json").exists() else [])


@pytest.mark.integration
@pytest.mark.parametrize("symbol", CSP_SYMBOLS, ids=_ids(CSP_SYMBOLS))
def test_csp_characterization(symbol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    expected = _load_expected("csp", symbol)
    captured_at = expected.get("captured_at") or expected["captured_on"]
    args = expected["args"]
    ohlc = _load_input("csp", symbol, "ohlc")
    chain = _load_input("csp", symbol, "chain")

    monkeypatch.setattr("services.data_service.get_ohlc", lambda s, period="1y": ohlc)
    monkeypatch.setattr("services.csp_service.get_ohlc", lambda s, period="1y": ohlc)
    monkeypatch.setattr(
        "services.options_service.get_all_expirations_data",
        lambda s, mn, mx: chain,
    )
    monkeypatch.setattr(
        "services.csp_service.get_all_expirations_data",
        lambda s, mn, mx: chain,
    )

    # Act
    with freeze_time(captured_at):
        from services.csp_service import process_symbol

        result, err = process_symbol(
            symbol,
            min_dte=args["min_dte"],
            max_dte=args["max_dte"],
            rf_rate=args["rf_rate"],
        )

    actual = _normalize(
        {"result": _to_jsonable(result), "error": _to_jsonable(err)}
    )
    expected_payload = _normalize(
        {"result": expected["result"], "error": expected["error"]}
    )

    # Assert
    assert actual == expected_payload


# --- CC ---------------------------------------------------------------------

CC_SYMBOLS = (_manifest()["tickers"]["cc"] if (SCREENER_ROOT / "manifest.json").exists() else [])


@pytest.mark.integration
@pytest.mark.parametrize("symbol", CC_SYMBOLS, ids=_ids(CC_SYMBOLS))
def test_cc_characterization(symbol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _load_expected("cc", symbol)
    captured_at = expected.get("captured_at") or expected["captured_on"]
    args = expected["args"]
    ohlc = _load_input("cc", symbol, "ohlc")
    chain = _load_input("cc", symbol, "chain")

    monkeypatch.setattr("services.data_service.get_ohlc", lambda s, period="1y": ohlc)
    monkeypatch.setattr("services.cc_service.get_ohlc", lambda s, period="1y": ohlc)
    monkeypatch.setattr(
        "services.options_service.get_all_expirations_calls_data",
        lambda s, mn, mx: chain,
    )
    monkeypatch.setattr(
        "services.cc_service.get_all_expirations_calls_data",
        lambda s, mn, mx: chain,
    )

    with freeze_time(captured_at):
        from services.cc_service import process_cc_symbol

        result, err = process_cc_symbol(
            symbol,
            min_dte=args["min_dte"],
            max_dte=args["max_dte"],
            rf_rate=args["rf_rate"],
        )

    actual = _normalize(
        {"result": _to_jsonable(result), "error": _to_jsonable(err)}
    )
    expected_payload = _normalize(
        {"result": expected["result"], "error": expected["error"]}
    )

    assert actual == expected_payload


# --- DITM -------------------------------------------------------------------

DITM_SYMBOLS = (_manifest()["tickers"]["ditm"] if (SCREENER_ROOT / "manifest.json").exists() else [])


@pytest.mark.integration
@pytest.mark.parametrize("symbol", DITM_SYMBOLS, ids=_ids(DITM_SYMBOLS))
def test_ditm_characterization(symbol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _load_expected("ditm", symbol)
    captured_at = expected.get("captured_at") or expected["captured_on"]
    args = expected["args"]
    macro_ctx = args["macro_context"]
    ohlc = _load_input("ditm", symbol, "ohlc")
    chain = _load_input("ditm", symbol, "chain")

    monkeypatch.setattr(
        "services.data_service.get_ohlc", lambda s, period="2y": ohlc
    )
    monkeypatch.setattr(
        "services.ditm_service.get_ohlc", lambda s, period="2y": ohlc
    )
    monkeypatch.setattr(
        "services.options_service.get_all_expirations_calls_data",
        lambda s, mn, mx: chain,
    )
    monkeypatch.setattr(
        "services.ditm_service.get_all_expirations_calls_data",
        lambda s, mn, mx: chain,
    )

    with freeze_time(captured_at):
        from services.ditm_service import process_symbol as process_ditm

        result, err = process_ditm(
            symbol,
            min_dte=args["min_dte"],
            max_dte=args["max_dte"],
            rf_rate=args["rf_rate"],
            macro_context=macro_ctx,
        )

    actual = _normalize(
        {"result": _to_jsonable(result), "error": _to_jsonable(err)}
    )
    expected_payload = _normalize(
        {"result": expected["result"], "error": expected["error"]}
    )

    assert actual == expected_payload
