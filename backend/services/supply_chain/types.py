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

from pydantic import BaseModel, ConfigDict, Field

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
    eight_k_failed_count: int = 0


@dataclass(frozen=True)
class EightKFetchResult:
    """Outcome of a parallel 8-K corpus fetch.

    ``successful`` preserves the original metadata order (filings whose
    HTTP fetch raised are simply omitted). ``failed_count`` is the
    number of metadata items whose fetch raised — surfaced on
    :class:`SupplyChainGraph` so the UI can flag partial corpora.
    """

    successful: list[tuple[dict, str]]
    failed_count: int


# --------------------------------------------------------- LLM result types --
# Pydantic models live here (rather than in ``llm_extractor.py``) because the
# legacy delegates in ``services/supply_chain_service.py`` import them, and we
# want a single types module for cross-collaborator use. Each model is the
# typed shape of one LLM pass response — names match the JSON keys the
# corresponding system prompt enumerates. Extra keys are tolerated so future
# prompt extensions don't crash old code.

_LLM_MODEL_CONFIG = ConfigDict(extra="ignore")


class LlmCompanyEntry(BaseModel):
    """One supplier / customer / competitor row inside an LLM response."""

    model_config = _LLM_MODEL_CONFIG

    name: str
    ticker: Optional[str] = None
    relationship: str = ""
    revenue_pct: Optional[float] = None
    cost_pct: Optional[float] = None
    notes: str = ""
    # ``source`` is intentionally optional with no default so dumps via
    # ``exclude_unset=True`` don't fabricate a "10-K" tag for industry-pass
    # rows that omit the field.
    source: Optional[str] = None
    segment: Optional[str] = None
    confidence: Optional[float] = None


class LlmFilingResult(BaseModel):
    """Output of the focal-company 10-K + 8-K extraction pass."""

    model_config = _LLM_MODEL_CONFIG

    segments: list[str] = Field(default_factory=list)
    concentration_note: str = ""
    suppliers: list[LlmCompanyEntry] = Field(default_factory=list)
    customers: list[LlmCompanyEntry] = Field(default_factory=list)
    competitors: list[LlmCompanyEntry] = Field(default_factory=list)
    summary: str = ""


class LlmIndustryResult(BaseModel):
    """Output of the industry-knowledge enrichment pass."""

    model_config = _LLM_MODEL_CONFIG

    suppliers: list[LlmCompanyEntry] = Field(default_factory=list)
    customers: list[LlmCompanyEntry] = Field(default_factory=list)
    competitors: list[LlmCompanyEntry] = Field(default_factory=list)


class LlmVerifierResult(BaseModel):
    """Output of the audit/verifier pass over the industry candidates."""

    model_config = _LLM_MODEL_CONFIG

    suppliers: list[LlmCompanyEntry] = Field(default_factory=list)
    customers: list[LlmCompanyEntry] = Field(default_factory=list)
    competitors: list[LlmCompanyEntry] = Field(default_factory=list)
    audit_summary: str = ""
