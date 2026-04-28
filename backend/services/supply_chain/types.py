"""Domain types for the supply-chain pipeline.

All types here are framework-agnostic and free of I/O. Routers serialise
``SupplyChainGraph`` via ``dataclasses.asdict``; bit-for-bit fixture
parity in [test_supply_chain_baseline.py](../../tests/integration/test_supply_chain_baseline.py)
depends on the field set / ordering of these dataclasses, so adding or
removing fields requires re-capturing every fixture (gate via
[ADR-0005, pending](../../../docs/adr/0005-supply-chain-fixture-policy.md)).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

SourceTag = Literal["10-K", "8-K", "industry"]


@dataclass
class CompanyNode:
    name: str
    ticker: Optional[str] = None
    relationship: str = ""        # e.g. "Foundry / chip fab", "Cloud customer"
    revenue_pct: Optional[float] = None  # % of focal company's revenue (if disclosed)
    cost_pct: Optional[float] = None     # % of focal company's COGS (if disclosed)
    notes: str = ""
    source: SourceTag = "10-K"           # provenance of this relationship
    segment: Optional[str] = None        # business segment, if known
    confidence: Optional[float] = None   # 0–1, only for inferred sources


@dataclass
class SupplyChainGraph:
    ticker: str
    company_name: str
    filing_date: str
    accession: str
    suppliers: list[CompanyNode] = field(default_factory=list)
    customers: list[CompanyNode] = field(default_factory=list)
    competitors: list[CompanyNode] = field(default_factory=list)
    summary: str = ""
    cached: bool = False
    eight_k_count: int = 0
    eight_k_dates: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    concentration_note: str = ""
    enrichment_used: list[str] = field(default_factory=list)
