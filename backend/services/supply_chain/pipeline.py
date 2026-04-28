"""Supply-chain orchestrator.

Composes :class:`SecDataClient` and :class:`LlmSupplyChainExtractor`
into the public ``get_supply_chain`` entry point. The orchestrator is
the only place where domain types (``CompanyNode``,
``SupplyChainGraph``) are assembled; collaborators only exchange
``Llm*Result`` Pydantic models and ``CompanyNode`` instances.

Tests inject fakes via the keyword-only ``sec_client`` / ``llm``
parameters; production code lets them default-construct from the
process-wide singletons.
"""
from __future__ import annotations

import logging
from typing import Optional

from .llm_extractor import LlmSupplyChainExtractor, get_default_extractor
from .sec_client import SecDataClient, get_default_client
from .types import (
    CompanyNode,
    LlmCompanyEntry,
    LlmFilingResult,
    LlmIndustryResult,
    LlmVerifierResult,
    SourceTag,
    SupplyChainGraph,
)

logger = logging.getLogger(__name__)


# -------------------------------------------------------------- merge utils
def _node_key(name: Optional[str], ticker: Optional[str]) -> str:
    if ticker:
        return f"T:{ticker.upper().strip()}"
    return f"N:{(name or '').lower().strip()}"


def _to_company_node(entry: LlmCompanyEntry, default_source: SourceTag) -> CompanyNode:
    return CompanyNode(
        name=entry.name,
        ticker=entry.ticker,
        relationship=entry.relationship,
        revenue_pct=entry.revenue_pct,
        cost_pct=entry.cost_pct,
        notes=entry.notes,
        source=entry.source or default_source,
        segment=entry.segment,
        confidence=entry.confidence,
    )


def _merge_industry(
    base: list[CompanyNode], additions: list[LlmCompanyEntry], cap: int
) -> list[CompanyNode]:
    """Append non-duplicate industry-pass entries to the filing-grounded list."""
    seen = {_node_key(n.name, n.ticker) for n in base}
    out = list(base)
    for entry in additions:
        key = _node_key(entry.name, entry.ticker)
        if key in seen:
            continue
        out.append(_to_company_node(entry, default_source="industry"))
        seen.add(key)
        if len(out) >= cap + len(base):
            break
    return out


# ---------------------------------------------------------------- helpers
def _resolve_focal_filing(
    sec: SecDataClient, ticker: str
) -> tuple[str, dict]:
    """Resolve ticker → CIK → latest 10-K. Raises ``ValueError`` on miss."""
    cik = sec.resolve_cik(ticker)
    if not cik:
        raise ValueError(f"Ticker {ticker} not found in SEC database")
    filing = sec.get_latest_10k(cik)
    if not filing:
        raise ValueError(f"No 10-K filing found for {ticker}")
    return cik, filing


def _load_8k_corpus(
    sec: SecDataClient, cik: str, filing: dict, ticker: str
) -> tuple[list[dict], str]:
    """Fetch up to 8 recent 8-Ks; return (metadata list, concatenated text)."""
    eight_ks = sec.get_recent_8ks(
        cik, since_date=filing["filing_date"], max_count=8
    )
    parts: list[str] = []
    for ek in eight_ks:
        try:
            t = sec.fetch_8k_text(ek["primary_doc_url"])
            parts.append(f"--- 8-K filed {ek['filing_date']} ---\n{t}")
        except Exception as e:  # noqa: BLE001 — phase 2c introduces parallel fetch with typed failure tracking
            logger.warning(
                "Failed to fetch 8-K %s for %s: %s", ek["accession"], ticker, e
            )
    text = "\n\n".join(parts)
    logger.info(
        "Loaded %d 8-Ks (%d chars total) for %s", len(eight_ks), len(text), ticker
    )
    return eight_ks, text


def _extract_filing_graph(
    llm: LlmSupplyChainExtractor,
    *,
    ticker: str,
    company_name: str,
    filing_text: str,
    eight_k_text: str,
) -> LlmFilingResult:
    logger.info("10-K text extracted: %d chars for %s", len(filing_text), ticker)
    return llm.extract_filing(
        ticker=ticker,
        company_name=company_name,
        filing_text=filing_text,
        recent_8k_text=eight_k_text,
    )


def _apply_industry_enrichment(
    llm: LlmSupplyChainExtractor,
    *,
    ticker: str,
    company_name: str,
    extracted: LlmFilingResult,
    suppliers: list[CompanyNode],
    customers: list[CompanyNode],
    competitors: list[CompanyNode],
    segments: list[str],
    enabled: bool,
) -> tuple[list[CompanyNode], list[CompanyNode], list[CompanyNode], list[str]]:
    """Run pass 2 + pass 3 if enabled; return merged lists + enrichment trail."""
    enrichment_used: list[str] = ["filing"]
    if not enabled:
        return suppliers, customers, competitors, enrichment_used

    try:
        industry = llm.enrich_industry(
            ticker=ticker,
            company_name=company_name,
            segments=segments,
            existing=extracted,
        )
    except Exception as e:  # noqa: BLE001 — phase 2c narrows to typed LLM errors
        logger.warning("Industry enrichment pass failed for %s: %s", ticker, e)
        return suppliers, customers, competitors, enrichment_used

    raw_counts = (
        len(industry.suppliers),
        len(industry.customers),
        len(industry.competitors),
    )
    pool: LlmIndustryResult | LlmVerifierResult = industry

    try:
        verified = llm.verify(
            ticker=ticker,
            company_name=company_name,
            candidates=industry,
        )
        ver_counts = (
            len(verified.suppliers),
            len(verified.customers),
            len(verified.competitors),
        )
        logger.info(
            "Verifier pass for %s: suppliers %d->%d, customers %d->%d, competitors %d->%d. %s",
            ticker,
            raw_counts[0], ver_counts[0],
            raw_counts[1], ver_counts[1],
            raw_counts[2], ver_counts[2],
            verified.audit_summary,
        )
        pool = verified
        enrichment_used.append("verified")
    except Exception as e:  # noqa: BLE001 — phase 2c narrows to typed LLM errors
        logger.warning(
            "Verifier pass failed for %s (using raw industry output): %s",
            ticker, e,
        )

    suppliers = _merge_industry(suppliers, list(pool.suppliers), cap=15)
    customers = _merge_industry(customers, list(pool.customers), cap=15)
    competitors = _merge_industry(competitors, list(pool.competitors), cap=5)
    enrichment_used.append("industry")
    logger.info(
        "Industry pass for %s: raw %d/%d/%d → merged %d/%d/%d (suppliers/customers/competitors)",
        ticker,
        raw_counts[0], raw_counts[1], raw_counts[2],
        len(pool.suppliers), len(pool.customers), len(pool.competitors),
    )

    return suppliers, customers, competitors, enrichment_used


# --------------------------------------------------------------- public API
def get_supply_chain(
    ticker: str,
    force_refresh: bool = False,  # noqa: ARG001 — reserved for future cache layer
    enrich_industry: bool = True,
    *,
    sec_client: Optional[SecDataClient] = None,
    llm: Optional[LlmSupplyChainExtractor] = None,
) -> SupplyChainGraph:
    ticker = ticker.upper()
    sec = sec_client or get_default_client()
    extractor = llm or get_default_extractor()

    cik, filing = _resolve_focal_filing(sec, ticker)
    company_name = filing["company_name"]
    filing_text = sec.fetch_filing_text(filing["primary_doc_url"])
    eight_ks, eight_k_text = _load_8k_corpus(sec, cik, filing, ticker)

    extracted = _extract_filing_graph(
        extractor,
        ticker=ticker,
        company_name=company_name,
        filing_text=filing_text,
        eight_k_text=eight_k_text,
    )

    suppliers = [
        _to_company_node(e, default_source="10-K") for e in extracted.suppliers
    ]
    customers = [
        _to_company_node(e, default_source="10-K") for e in extracted.customers
    ]
    competitors = [
        _to_company_node(e, default_source="10-K") for e in extracted.competitors
    ]
    segments = [s for s in extracted.segments if isinstance(s, str) and s.strip()]

    suppliers, customers, competitors, enrichment_used = _apply_industry_enrichment(
        extractor,
        ticker=ticker,
        company_name=company_name,
        extracted=extracted,
        suppliers=suppliers,
        customers=customers,
        competitors=competitors,
        segments=segments,
        enabled=enrich_industry,
    )

    return SupplyChainGraph(
        ticker=ticker,
        company_name=company_name,
        filing_date=filing["filing_date"],
        accession=filing["accession"],
        suppliers=suppliers,
        customers=customers,
        competitors=competitors,
        summary=extracted.summary,
        cached=False,
        eight_k_count=len(eight_ks),
        eight_k_dates=[ek["filing_date"] for ek in eight_ks],
        segments=segments,
        concentration_note=extracted.concentration_note or "",
        enrichment_used=enrichment_used,
    )
