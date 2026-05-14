"""Shared fixtures for the conviction-classifier worker tests.

External services (Azure OpenAI / Cosmos / Key Vault) are mocked. No network
calls are made. See .github/instructions/tests.instructions.md.

Both workers/classifier and workers/narrative-detector ship flat modules
named ``main``, ``config``, ``cosmos_client``, etc. When both worker test
suites run in the same pytest session, the import cache can hold the sibling
worker's copy, which breaks lazy ``from main import main`` calls inside test
fixtures. The autouse fixture below evicts ONLY sibling-worker entries from
sys.modules and pins sys.path to this worker's root before each test.
Worker-local modules (``classifier``) stay cached so module-top imports in
test files remain bound to a module whose globals participate in
unittest.mock patches.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
_SIBLING_MODULE_NAMES = ("main", "config", "cosmos_client", "kv_secrets")


@pytest.fixture(autouse=True)
def _pin_classifier_root() -> None:
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
