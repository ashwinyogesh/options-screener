"""Supply Chain extraction service — re-export shim (DEPRECATED).

The supply-chain pipeline lives in :mod:`services.supply_chain`. This
module survives only so existing import paths
(``from services.supply_chain_service import get_supply_chain``) keep
working through the Phase 1 / Phase 2 transition. Scheduled for
removal once the router is migrated and the Phase 0 fixture mocks
target the new package directly.

See [docs/adr/0003-supply-chain-adapter-pattern.md](../../docs/adr/0003-supply-chain-adapter-pattern.md)
(pending) for the rationale.
"""
from __future__ import annotations

from services.supply_chain.llm_extractor import (
    LlmSupplyChainExtractor,
    get_default_extractor,
)
from services.supply_chain.pipeline import get_supply_chain
from services.supply_chain.sec_client import SecDataClient, get_default_client
from services.supply_chain.types import (
    CompanyNode,
    LlmCompanyEntry,
    LlmFilingResult,
    LlmIndustryResult,
    LlmVerifierResult,
    SourceTag,
    SupplyChainGraph,
)

__all__ = [
    "CompanyNode",
    "LlmCompanyEntry",
    "LlmFilingResult",
    "LlmIndustryResult",
    "LlmSupplyChainExtractor",
    "LlmVerifierResult",
    "SecDataClient",
    "SourceTag",
    "SupplyChainGraph",
    "get_default_client",
    "get_default_extractor",
    "get_supply_chain",
    "resolve_cik",
]


def resolve_cik(ticker: str) -> str | None:
    """Back-compat shim around :meth:`SecDataClient.resolve_cik`."""
    return get_default_client().resolve_cik(ticker)
