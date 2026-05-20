"""Shared fixtures for the narrative-backfill worker tests.

Pins ``sys.path`` to this worker's root and evicts sibling-worker copies of
flat module names so lazy imports in tests resolve here.  Mirrors the
narrative-detector conftest.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
_SIBLING_MODULE_NAMES = ("main", "config", "cosmos_client", "price_fetcher")


@pytest.fixture(autouse=True)
def _pin_backfill_root() -> None:
    if _WORKER_ROOT in sys.path:
        sys.path.remove(_WORKER_ROOT)
    sys.path.insert(0, _WORKER_ROOT)
    for name in _SIBLING_MODULE_NAMES:
        module = sys.modules.get(name)
        if module is None:
            continue
        module_file = getattr(module, "__file__", "") or ""
        if _WORKER_ROOT not in module_file:
            del sys.modules[name]
