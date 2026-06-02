"""Filings intelligence orchestrator.

Public API:
    get_intel(ticker, insight_type) -> IntelResult

Behaviour:
  1. Build the cache key — for filing-bound insights this includes the
     accession#; for the multi-filing ``leadership`` insight we include
     both the DEF 14A accession and the Form 4 window.
  2. Check Cosmos (or the in-memory fallback) for a cached result.
  3. On miss: fetch the needed filing text(s), run a single Azure OpenAI
     call with the per-insight prompt + schema, persist, return.

Errors raised:
  * ``InvalidInsightType``      → 422
  * ``FilingNotFound``          → 404
  * ``FilingsIntelUnavailable`` → 503
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from azure.cosmos import exceptions as cosmos_exceptions

from services.dd_coach.filings_intel import cosmos as intel_cosmos
from services.dd_coach.filings_intel.errors import (
    FilingNotFound,
    FilingsIntelUnavailable,
    InvalidInsightType,
)
from services.dd_coach.filings_intel.fetcher import (
    FilingsFetcher,
    Form4Summary,
    TenKBundle,
    get_default_fetcher,
)
from services.dd_coach.filings_intel.prompts import (
    SCHEMAS,
    SYSTEM_PROMPTS,
    VALID_INSIGHT_TYPES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntelResult:
    ticker: str
    insight_type: str
    cache_key: str
    sources: list[dict[str, str]]
    content: dict[str, Any]
    generated_at: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# LLM invocation — injectable for tests
# ---------------------------------------------------------------------------

LlmCallable = Callable[..., dict[str, Any]]
_llm_override: Optional[LlmCallable] = None


def _default_llm(*, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    # Lazy import keeps the etv package out of import-time for tests that
    # don't exercise LLM paths.
    from services.etv.llm import call_json
    return call_json(system=system, user=user, schema=schema)


def set_llm_for_tests(fn: Optional[LlmCallable]) -> None:
    global _llm_override
    _llm_override = fn


def _call_llm(*, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    fn = _llm_override or _default_llm
    try:
        return fn(system=system, user=user, schema=schema)
    except Exception as exc:
        raise FilingsIntelUnavailable(f"Azure OpenAI call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_id(ticker: str, cache_key: str, insight_type: str) -> str:
    return f"{ticker}|{cache_key}|{insight_type}"


def _cache_read(ticker: str, cache_key: str, insight_type: str) -> Optional[dict[str, Any]]:
    cid = _doc_id(ticker, cache_key, insight_type)
    try:
        doc = intel_cosmos.get_intel_container().read_item(item=cid, partition_key=ticker)
        return doc
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return None
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        logger.warning("filings_intel cache read failed (%s): %s", cid, exc)
        return None


def _cache_write(
    *,
    ticker: str,
    cache_key: str,
    insight_type: str,
    sources: list[dict[str, str]],
    content: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "id": _doc_id(ticker, cache_key, insight_type),
        "ticker": ticker,
        "cache_key": cache_key,
        "insight_type": insight_type,
        "sources": sources,
        "content": content,
        "generated_at": _now_iso(),
    }
    try:
        intel_cosmos.get_intel_container().upsert_item(body=body)
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        logger.warning("filings_intel cache write failed: %s", exc)
    return body


# ---------------------------------------------------------------------------
# Insight handlers
# ---------------------------------------------------------------------------


def _src(form: str, ref: Any) -> dict[str, str]:
    return {
        "form": form,
        "accession": ref.accession,
        "filing_date": ref.filing_date,
        "primary_doc_url": ref.primary_doc_url,
    }


def _do_business_summary(ticker: str, f: FilingsFetcher) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    bundle: TenKBundle = f.get_10k(ticker, prior=False)
    if not bundle.sections.business:
        raise FilingNotFound(
            f"Business section (Item 1) could not be extracted from latest 10-K for {ticker}.",
        )
    cache_key = bundle.ref.accession
    user = (
        f"Ticker: {ticker}\n"
        f"Filing date: {bundle.ref.filing_date}\n\n"
        f"--- Item 1: Business ---\n{bundle.sections.business}"
    )
    content = _call_llm(
        system=SYSTEM_PROMPTS["business_summary"],
        user=user,
        schema=SCHEMAS["business_summary"],
    )
    return cache_key, [_src("10-K", bundle.ref)], content


def _do_risk_diff(ticker: str, f: FilingsFetcher) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    latest = f.get_10k(ticker, prior=False)
    prior = f.get_10k(ticker, prior=True)
    if not latest.sections.risk_factors or not prior.sections.risk_factors:
        raise FilingNotFound(
            f"Risk Factors (Item 1A) missing from one of the 10-Ks for {ticker}.",
        )
    # v3 prefix — schema gained ongoing_risks bucket.
    cache_key = f"v3_{latest.ref.accession}_vs_{prior.ref.accession}"
    user = (
        f"Ticker: {ticker}\n\n"
        f"=== THIS YEAR'S 10-K ({latest.ref.filing_date}) Risk Factors ===\n"
        f"{latest.sections.risk_factors}\n\n"
        f"=== PRIOR YEAR'S 10-K ({prior.ref.filing_date}) Risk Factors ===\n"
        f"{prior.sections.risk_factors}"
    )
    content = _call_llm(
        system=SYSTEM_PROMPTS["risk_diff"],
        user=user,
        schema=SCHEMAS["risk_diff"],
    )
    return cache_key, [_src("10-K", latest.ref), _src("10-K", prior.ref)], content


def _do_mda_summary(ticker: str, f: FilingsFetcher) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    # Prefer the latest 10-Q for freshness; fall back to 10-K MD&A.
    sources: list[dict[str, str]] = []
    try:
        ref, mda = f.get_10q_mda(ticker)
        sources.append(_src("10-Q", ref))
        cache_key = ref.accession
        mda_text = mda
    except FilingNotFound:
        bundle = f.get_10k(ticker, prior=False)
        if not bundle.sections.mda:
            raise FilingNotFound(
                f"MD&A (Item 7) could not be extracted from latest 10-K for {ticker}.",
            )
        sources.append(_src("10-K", bundle.ref))
        cache_key = bundle.ref.accession
        mda_text = bundle.sections.mda

    if not mda_text:
        raise FilingNotFound(f"MD&A text empty for {ticker}")
    user = f"Ticker: {ticker}\n\n--- MD&A ---\n{mda_text}"
    content = _call_llm(
        system=SYSTEM_PROMPTS["mda_summary"],
        user=user,
        schema=SCHEMAS["mda_summary"],
    )
    return cache_key, sources, content


def _do_leadership(ticker: str, f: FilingsFetcher) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    ref, proxy_text = f.get_def14a(ticker)
    form4 = f.get_form4_summary(ticker, window_days=180)
    cache_key = f"{ref.accession}_f4_{form4.filings_count}"
    form4_block = (
        f"Form 4 metadata (last {form4.window_days} days): "
        f"{form4.filings_count} filings, most recent {form4.most_recent_date or 'none'}.\n"
        + "\n".join(
            f"  - {r.filing_date}: {r.accession}" for r in form4.filings[:20]
        )
    )
    user = (
        f"Ticker: {ticker}\n\n"
        f"=== DEF 14A ({ref.filing_date}) ===\n{proxy_text}\n\n"
        f"=== {form4_block} ==="
    )
    content = _call_llm(
        system=SYSTEM_PROMPTS["leadership"],
        user=user,
        schema=SCHEMAS["leadership"],
    )
    sources: list[dict[str, str]] = [_src("DEF 14A", ref)]
    if form4.filings_count:
        sources.append({
            "form": "4",
            "accession": "(metadata roll-up)",
            "filing_date": form4.most_recent_date or "",
            "primary_doc_url": "",
        })
    return cache_key, sources, content


def _do_bear_scaffold(ticker: str, f: FilingsFetcher) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    bundle = f.get_10k(ticker, prior=False)
    if not (bundle.sections.business or bundle.sections.risk_factors):
        raise FilingNotFound(f"Business + Risk Factors required for bear scaffold ({ticker}).")
    cache_key = bundle.ref.accession
    user = (
        f"Ticker: {ticker}\n\n"
        f"--- Item 1: Business (truncated) ---\n{bundle.sections.business[:20_000]}\n\n"
        f"--- Item 1A: Risk Factors ---\n{bundle.sections.risk_factors}"
    )
    content = _call_llm(
        system=SYSTEM_PROMPTS["bear_scaffold"],
        user=user,
        schema=SCHEMAS["bear_scaffold"],
    )
    return cache_key, [_src("10-K", bundle.ref)], content


_HANDLERS: dict[str, Callable[[str, FilingsFetcher], tuple[str, list[dict[str, str]], dict[str, Any]]]] = {
    "business_summary": _do_business_summary,
    "risk_diff": _do_risk_diff,
    "mda_summary": _do_mda_summary,
    "leadership": _do_leadership,
    "bear_scaffold": _do_bear_scaffold,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_intel(
    ticker: str,
    insight_type: str,
    *,
    fetcher: Optional[FilingsFetcher] = None,
    force: bool = False,
) -> IntelResult:
    """Return a (cached or freshly computed) insight for a ticker.

    Args:
        ticker: Stock symbol (case-insensitive).
        insight_type: One of :data:`VALID_INSIGHT_TYPES`.
        fetcher: Override for tests; production uses the default singleton.
        force: When True, bypass the cache and recompute. Result is still
            persisted, overwriting the prior entry.
    """
    sym = ticker.strip().upper()
    if insight_type not in VALID_INSIGHT_TYPES:
        raise InvalidInsightType(
            f"Unknown insight_type {insight_type!r}; expected one of "
            f"{', '.join(VALID_INSIGHT_TYPES)}.",
        )
    if not sym:
        raise InvalidInsightType("ticker must be non-empty")

    f = fetcher or get_default_fetcher()
    handler = _HANDLERS[insight_type]

    # Peek the accession before LLM-ing to compute a stable cache_key.
    # Each handler does its own fetch+LLM; for cache hit detection we need
    # the key first, so we use a two-step pattern: handlers expose the
    # cache_key as their first return alongside the content. To avoid
    # paying for the LLM call on a hit, handlers MUST be cheap on the
    # fetch path (disk cache makes them so) and we accept the cost of one
    # extra fetch on a cosmos miss. The fetch is on-disk-cached, so cost
    # is bounded to the first ever request per accession.

    # First pass: just to learn the cache_key — we compute it ourselves
    # from the filing(s) the handler would use. To keep code DRY we
    # actually run the handler once and check cache after the fetch but
    # before the LLM. Cleanest implementation: handlers call _call_llm
    # internally; we wrap with cache-aware shim below.
    #
    # Simpler approach: run a "key-only" pre-step by introspecting the
    # latest filing refs for each insight type.
    cache_key = _peek_cache_key(sym, insight_type, f)

    if not force:
        cached = _cache_read(sym, cache_key, insight_type)
        if cached is not None:
            return IntelResult(
                ticker=sym,
                insight_type=insight_type,
                cache_key=cache_key,
                sources=list(cached.get("sources", [])),
                content=dict(cached.get("content", {})),
                generated_at=str(cached.get("generated_at", _now_iso())),
                cached=True,
            )

    actual_key, sources, content = handler(sym, f)
    # If the peek's key disagrees (rare race), trust the handler's.
    final_key = actual_key or cache_key
    doc = _cache_write(
        ticker=sym,
        cache_key=final_key,
        insight_type=insight_type,
        sources=sources,
        content=content,
    )
    return IntelResult(
        ticker=sym,
        insight_type=insight_type,
        cache_key=final_key,
        sources=sources,
        content=content,
        generated_at=doc["generated_at"],
        cached=False,
    )


def _peek_cache_key(ticker: str, insight_type: str, f: FilingsFetcher) -> str:
    """Cheap pre-LLM lookup of the cache key for a given (ticker, insight)."""
    if insight_type == "business_summary" or insight_type == "bear_scaffold":
        return f.get_10k(ticker, prior=False).ref.accession
    if insight_type == "risk_diff":
        latest = f.get_10k(ticker, prior=False)
        prior = f.get_10k(ticker, prior=True)
        return f"v3_{latest.ref.accession}_vs_{prior.ref.accession}"
    if insight_type == "mda_summary":
        try:
            ref, _ = f.get_10q_mda(ticker)
            return ref.accession
        except FilingNotFound:
            return f.get_10k(ticker, prior=False).ref.accession
    if insight_type == "leadership":
        ref, _ = f.get_def14a(ticker)
        f4 = f.get_form4_summary(ticker, window_days=180)
        return f"{ref.accession}_f4_{f4.filings_count}"
    raise InvalidInsightType(insight_type)  # unreachable
