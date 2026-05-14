"""Shared fixtures for the conviction-classifier worker tests.

Tests run against in-process fakes — no real Azure OpenAI / Cosmos / Key Vault
calls are made. See .github/instructions/tests.instructions.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the worker package importable as flat modules (mirrors how it runs in
# the container: `python -u main.py`).
_WORKER_ROOT = Path(__file__).resolve().parent.parent
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))
