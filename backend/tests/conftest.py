"""
Pytest configuration and shared fixtures for the Options Screener backend.

Test conventions: see .github/instructions/tests.instructions.md
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from typing import Any

import pytest

# Make `backend/` importable as the package root so tests can do
# `from services.csp_service import process_symbol`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


FIXTURES_ROOT = Path(__file__).parent / "fixtures"


def fixture_path(*parts: str) -> Path:
    """Return an absolute path under backend/tests/fixtures/."""
    return FIXTURES_ROOT.joinpath(*parts)


def load_pickle(path: Path) -> Any:
    """Read a pickle fixture; raises FileNotFoundError with a helpful hint."""
    if not path.exists():
        raise FileNotFoundError(
            f"Missing fixture: {path}. "
            "Re-run scripts/capture_screener_fixtures.py to regenerate."
        )
    with path.open("rb") as fh:
        return pickle.load(fh)


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing fixture: {path}. "
            "Re-run scripts/capture_screener_fixtures.py to regenerate."
        )
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fixtures_root() -> Path:
    return FIXTURES_ROOT
