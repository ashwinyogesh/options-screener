"""Supply chain extraction package.

Phase 1 refactor: decomposes the legacy ``supply_chain_service`` monolith
into focused collaborators (types, text extraction, SEC client, LLM
extractor, pipeline). See `docs/adr/0003-supply-chain-adapter-pattern.md`
(pending) and ``backend/tests/integration/test_supply_chain_baseline.py``
for the contract this refactor must preserve.
"""
from __future__ import annotations

from .types import CompanyNode, SourceTag, SupplyChainGraph

__all__ = ["CompanyNode", "SourceTag", "SupplyChainGraph"]
