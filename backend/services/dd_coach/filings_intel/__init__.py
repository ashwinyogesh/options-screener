"""LLM-powered filings intelligence for DD Coach (V3).

Public surface:
    get_intel(ticker, insight_type) -> IntelResult

Insight types:
    - business_summary  : plain-English what the company does
    - risk_diff         : risks NEW in this 10-K vs prior year
    - mda_summary       : revenue bridge + margin drivers + liquidity
    - leadership        : CEO / comp alignment / insider signal
    - bear_scaffold     : 3 plausible 50%-loss scenarios

Storage: Cosmos container ``dd_filings_intel`` keyed by
``{ticker}|{accession_or_period}|{insight_type}`` — first call computes &
persists; subsequent calls return cached. Raw filing text is cached on
disk under ``data/dd_filings_cache/`` keyed by accession.
"""
from services.dd_coach.filings_intel.service import IntelResult, get_intel  # noqa: F401
