"""
One-off capture utility for screener characterization fixtures.

Run from repo root with:
    cd backend
    .\venv\Scripts\python.exe ..\scripts\capture_screener_fixtures.py

What it does:
- For each (screener, ticker) pair below, calls live yfinance via the real
  data_service / options_service ONCE.
- Pickles the inputs (OHLC DataFrame, options chain list-of-dicts).
- Calls the corresponding process_* function while monkeypatching the I/O
  functions to return the captured pickles. This guarantees the saved JSON
  output is reproducible from the pickles — without this, yfinance can
  return slightly different data on consecutive live calls and the test
  drifts from the capture.
- Dumps the result dataclass as JSON for byte-comparison in tests.

Why this exists:
- The three screener services have ~95% duplicated orchestration. Before refactoring
  them into a single ScreenerService, we need a safety net that proves the new
  implementation produces identical output for a fixed set of inputs.
- Tests must not call yfinance. Capture once, lock the bytes in fixtures/,
  tests monkeypatch the same I/O surfaces.

Re-running this script regenerates fixtures. Do that intentionally — never as
part of CI.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import pickle
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from freezegun import freeze_time

# Make backend importable
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

from services import data_service, options_service  # noqa: E402
from services.csp_service import process_symbol as process_csp  # noqa: E402
from services.cc_service import process_cc_symbol as process_cc  # noqa: E402
from services.ditm_service import (  # noqa: E402
    get_macro_context,
    process_symbol as process_ditm,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("capture")


# 3 tickers per screener (overlap is fine).
# CSP/CC: a high-IV growth, a stable mega-cap, a low-IV utility — covers
# different scoring regimes.
# DITM: trending names suitable for a long-call thesis.
TICKERS = {
    "csp": ["NVDA", "AAPL", "DUK"],
    "cc": ["NVDA", "AAPL", "DUK"],
    "ditm": ["NVDA", "AAPL", "MSFT"],
}

# Default DTE windows (must match service defaults).
WINDOWS = {
    "csp": (30, 60),
    "cc": (30, 60),
    "ditm": (90, 180),
}

FIXTURES_ROOT = _BACKEND / "tests" / "fixtures" / "screener"
INPUTS_DIR = FIXTURES_ROOT / "inputs"
OUTPUTS_DIR = FIXTURES_ROOT / "outputs"


def _ensure_dirs() -> None:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_pickle(path: Path):
    with path.open("rb") as fh:
        return pickle.load(fh)


def _to_jsonable(obj):
    """Convert dataclasses + nested structures to JSON-friendly types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    return obj


def _capture_inputs(symbol: str, kind: str, min_dte: int, max_dte: int) -> dict:
    """Fetch live OHLC and options chain, pickle them, return a manifest."""
    log.info("[%s/%s] fetching OHLC", kind, symbol)
    ohlc_period = "2y" if kind == "ditm" else "1y"
    ohlc = data_service.get_ohlc(symbol, period=ohlc_period)

    log.info("[%s/%s] fetching options chain", kind, symbol)
    if kind == "csp":
        chain = options_service.get_all_expirations_data(symbol, min_dte, max_dte)
    else:  # cc + ditm both use calls
        chain = options_service.get_all_expirations_calls_data(symbol, min_dte, max_dte)

    ohlc_path = INPUTS_DIR / f"{kind}__{symbol}__ohlc.pkl"
    chain_path = INPUTS_DIR / f"{kind}__{symbol}__chain.pkl"

    with ohlc_path.open("wb") as fh:
        pickle.dump(ohlc, fh)
    with chain_path.open("wb") as fh:
        pickle.dump(chain, fh)

    log.info(
        "[%s/%s] inputs saved (ohlc=%d rows, chain=%d expirations)",
        kind,
        symbol,
        len(ohlc),
        len(chain),
    )
    return {
        "ohlc": str(ohlc_path.relative_to(_BACKEND)),
        "chain": str(chain_path.relative_to(_BACKEND)),
    }


def _capture_csp(symbol: str, captured_at: str) -> None:
    min_dte, max_dte = WINDOWS["csp"]
    _capture_inputs(symbol, "csp", min_dte, max_dte)
    rf_rate = 0.045  # locked, not fetched
    log.info("[csp/%s] running process_symbol", symbol)
    ohlc = _load_pickle(INPUTS_DIR / f"csp__{symbol}__ohlc.pkl")
    chain = _load_pickle(INPUTS_DIR / f"csp__{symbol}__chain.pkl")
    with (
        freeze_time(captured_at),
        patch("services.csp_service.get_ohlc", lambda s, period="1y": ohlc),
        patch(
            "services.csp_service.get_all_expirations_data",
            lambda s, mn, mx: chain,
        ),
    ):
        result, err = process_csp(
            symbol, min_dte=min_dte, max_dte=max_dte, rf_rate=rf_rate
        )
    payload = {
        "args": {"min_dte": min_dte, "max_dte": max_dte, "rf_rate": rf_rate},
        "captured_at": captured_at,
        "captured_on": captured_at[:10],
        "result": _to_jsonable(result),
        "error": _to_jsonable(err),
    }
    out = OUTPUTS_DIR / f"csp__{symbol}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info(
        "[csp/%s] %d expirations, err=%s",
        symbol,
        len(result),
        err.reason if err else "none",
    )


def _capture_cc(symbol: str, captured_at: str) -> None:
    min_dte, max_dte = WINDOWS["cc"]
    _capture_inputs(symbol, "cc", min_dte, max_dte)
    rf_rate = 0.045
    log.info("[cc/%s] running process_cc_symbol", symbol)
    ohlc = _load_pickle(INPUTS_DIR / f"cc__{symbol}__ohlc.pkl")
    chain = _load_pickle(INPUTS_DIR / f"cc__{symbol}__chain.pkl")
    with (
        freeze_time(captured_at),
        patch("services.cc_service.get_ohlc", lambda s, period="1y": ohlc),
        patch(
            "services.cc_service.get_all_expirations_calls_data",
            lambda s, mn, mx: chain,
        ),
    ):
        result, err = process_cc(
            symbol, min_dte=min_dte, max_dte=max_dte, rf_rate=rf_rate
        )
    payload = {
        "args": {"min_dte": min_dte, "max_dte": max_dte, "rf_rate": rf_rate},
        "captured_at": captured_at,
        "captured_on": captured_at[:10],
        "result": _to_jsonable(result),
        "error": _to_jsonable(err),
    }
    out = OUTPUTS_DIR / f"cc__{symbol}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info(
        "[cc/%s] %d expirations, err=%s",
        symbol,
        len(result),
        err.reason if err else "none",
    )


def _capture_ditm(symbol: str, macro_ctx: dict, captured_at: str) -> None:
    min_dte, max_dte = WINDOWS["ditm"]
    _capture_inputs(symbol, "ditm", min_dte, max_dte)
    rf_rate = 0.045
    log.info("[ditm/%s] running process_symbol", symbol)
    ohlc = _load_pickle(INPUTS_DIR / f"ditm__{symbol}__ohlc.pkl")
    chain = _load_pickle(INPUTS_DIR / f"ditm__{symbol}__chain.pkl")
    with (
        freeze_time(captured_at),
        patch("services.ditm_service.get_ohlc", lambda s, period="2y": ohlc),
        patch(
            "services.ditm_service.get_all_expirations_calls_data",
            lambda s, mn, mx: chain,
        ),
    ):
        result, err = process_ditm(
            symbol,
            min_dte=min_dte,
            max_dte=max_dte,
            rf_rate=rf_rate,
            macro_context=macro_ctx,
        )
    payload = {
        "args": {
            "min_dte": min_dte,
            "max_dte": max_dte,
            "rf_rate": rf_rate,
            "macro_context": macro_ctx,
        },
        "captured_at": captured_at,
        "captured_on": captured_at[:10],
        "result": _to_jsonable(result),
        "error": _to_jsonable(err),
    }
    out = OUTPUTS_DIR / f"ditm__{symbol}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info(
        "[ditm/%s] %d expirations, err=%s",
        symbol,
        len(result),
        err.reason if err else "none",
    )


def main() -> int:
    _ensure_dirs()
    # Capture an explicit UTC datetime (not just a date). Tests freeze to this
    # exact instant — using just a date causes UTC midnight to fall on the
    # previous day in ET, which shifts DTE / earnings comparisons.
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    captured_on = captured_at[:10]
    log.info("Capture instant (frozen for tests): %s", captured_at)

    # Capture macro context once for DITM and pickle for tests.
    log.info("Fetching macro context (VIX/SPY)")
    macro_ctx = get_macro_context()
    macro_path = INPUTS_DIR / "ditm__macro_context.json"
    macro_path.write_text(
        json.dumps(macro_ctx, indent=2, default=str), encoding="utf-8"
    )
    log.info("Macro context: %s", macro_ctx)

    # Persist a manifest with the freeze instant so tests use the same `now`.
    manifest = {
        "captured_at": captured_at,
        "captured_on": captured_on,
        "tickers": TICKERS,
        "windows": WINDOWS,
    }
    (FIXTURES_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    failures: list[tuple[str, str, str]] = []

    for sym in TICKERS["csp"]:
        try:
            _capture_csp(sym, captured_at)
        except Exception as exc:
            log.exception("CSP capture failed for %s", sym)
            failures.append(("csp", sym, str(exc)))

    for sym in TICKERS["cc"]:
        try:
            _capture_cc(sym, captured_at)
        except Exception as exc:
            log.exception("CC capture failed for %s", sym)
            failures.append(("cc", sym, str(exc)))

    for sym in TICKERS["ditm"]:
        try:
            _capture_ditm(sym, macro_ctx, captured_at)
        except Exception as exc:
            log.exception("DITM capture failed for %s", sym)
            failures.append(("ditm", sym, str(exc)))

    if failures:
        log.error("Failures during capture:")
        for kind, sym, msg in failures:
            log.error("  %s/%s: %s", kind, sym, msg)
        return 1

    log.info("Capture complete. Fixtures under %s", FIXTURES_ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
