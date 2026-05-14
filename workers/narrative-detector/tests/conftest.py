"""Shared fixtures for the narrative-detector worker tests.

Both workers/classifier and workers/narrative-detector ship flat modules
named ``main``, ``config``, ``cosmos_client``, etc. The autouse fixture
below pins sys.path to this worker's root and evicts sibling-worker copies
from sys.modules so lazy imports in tests resolve here. Worker-local
``detector`` stays cached so unittest.mock patches on already-imported
references continue to work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
_SIBLING_MODULE_NAMES = ("main", "config", "cosmos_client", "kv_secrets")


@pytest.fixture(autouse=True)
def _pin_detector_root() -> None:
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
