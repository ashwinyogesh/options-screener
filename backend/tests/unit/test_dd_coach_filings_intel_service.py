"""Unit tests for filings_intel.service — orchestrator + cache layer.

We stub the fetcher (no SEC network) and the LLM call (no Azure OpenAI),
then exercise:
  - first call writes to the in-memory cache, returns cached=False
  - second call returns cached=True without calling the LLM again
  - force=True bypasses cache
  - validation rejects unknown insight_type / empty ticker
  - 503 propagates when LLM raises
  - missing prior 10-K → 404
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from services.dd_coach.filings_intel import cosmos as intel_cosmos
from services.dd_coach.filings_intel import service as svc
from services.dd_coach.filings_intel.errors import (
    FilingNotFound,
    FilingsIntelUnavailable,
    InvalidInsightType,
)
from services.dd_coach.filings_intel.fetcher import (
    FilingRef,
    FilingsFetcher,
    Form4Summary,
    TenKBundle,
)
from services.dd_coach.filings_intel.sections import FilingSections


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _ref(form: str, acc: str, date: str = "2025-01-15") -> FilingRef:
    return FilingRef(
        accession=acc,
        filing_date=date,
        primary_doc_url=f"https://sec.gov/{form}/{acc}",
        form=form,
    )


@dataclass
class FakeFetcher:
    """A FilingsFetcher-shaped stub with controllable per-method outputs."""

    latest_10k: TenKBundle | None = None
    prior_10k: TenKBundle | None = None
    q10_mda: tuple[FilingRef, str] | None = None
    def14a: tuple[FilingRef, str] | None = None
    form4: Form4Summary = Form4Summary(window_days=180, filings_count=0, most_recent_date=None)

    def get_10k(self, ticker: str, *, prior: bool = False) -> TenKBundle:
        bundle = self.prior_10k if prior else self.latest_10k
        if bundle is None:
            raise FilingNotFound(f"no 10-K (prior={prior})")
        return bundle

    def get_10q_mda(self, ticker: str) -> tuple[FilingRef, str]:
        if self.q10_mda is None:
            raise FilingNotFound("no 10-Q")
        return self.q10_mda

    def get_def14a(self, ticker: str) -> tuple[FilingRef, str]:
        if self.def14a is None:
            raise FilingNotFound("no DEF 14A")
        return self.def14a

    def get_form4_summary(self, ticker: str, *, window_days: int = 180) -> Form4Summary:
        return self.form4


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class CacheStub:
    """Mirrors the in-memory intel container surface used by the service."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.read_calls = 0
        self.write_calls = 0

    def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self.write_calls += 1
        self.items[(body["ticker"], body["id"])] = dict(body)
        return dict(body)

    def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        self.read_calls += 1
        doc = self.items.get((partition_key, item))
        if doc is None:
            from azure.cosmos import exceptions
            raise exceptions.CosmosResourceNotFoundError(status_code=404, message="nf")
        return dict(doc)


@pytest.fixture
def cache() -> CacheStub:
    c = CacheStub()
    intel_cosmos.set_container_for_tests(c)
    yield c
    intel_cosmos.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_llm() -> None:
    yield
    svc.set_llm_for_tests(None)


def _bundle(acc: str, *, has_business: bool = True, has_risk: bool = True, has_mda: bool = True) -> TenKBundle:
    return TenKBundle(
        ref=_ref("10-K", acc),
        sections=FilingSections(
            business="We make widgets." if has_business else "",
            risk_factors="Risk: competition." if has_risk else "",
            mda="Revenue up 10%." if has_mda else "",
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_business_summary_first_call_calls_llm_and_caches(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))
    calls = {"n": 0}

    def fake_llm(*, system: str, user: str, schema: dict) -> dict:
        calls["n"] += 1
        return {
            "summary": "Sells widgets.",
            "primary_products": ["Widget A"],
            "main_customers": "Industrial buyers.",
            "moat_hypothesis": "Distribution scale.",
            "segments": [],
        }

    svc.set_llm_for_tests(fake_llm)
    result = svc.get_intel("MSFT", "business_summary", fetcher=fetcher)

    assert result.ticker == "MSFT"
    assert result.cached is False
    assert result.cache_key == "0000-25-001"
    assert result.content["summary"] == "Sells widgets."
    assert calls["n"] == 1
    assert cache.write_calls == 1


def test_business_summary_second_call_hits_cache_without_llm(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))
    calls = {"n": 0}

    def fake_llm(*, system: str, user: str, schema: dict) -> dict:
        calls["n"] += 1
        return {
            "summary": "x",
            "primary_products": [],
            "main_customers": "x",
            "moat_hypothesis": "x",
            "segments": [],
        }

    svc.set_llm_for_tests(fake_llm)
    svc.get_intel("MSFT", "business_summary", fetcher=fetcher)
    second = svc.get_intel("MSFT", "business_summary", fetcher=fetcher)

    assert second.cached is True
    assert calls["n"] == 1  # second call did NOT invoke LLM


def test_force_recomputes_even_if_cached(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))
    counts = {"n": 0}

    def fake_llm(*, system: str, user: str, schema: dict) -> dict:
        counts["n"] += 1
        return {
            "summary": f"call {counts['n']}",
            "primary_products": [],
            "main_customers": "",
            "moat_hypothesis": "",
            "segments": [],
        }

    svc.set_llm_for_tests(fake_llm)
    svc.get_intel("MSFT", "business_summary", fetcher=fetcher)
    forced = svc.get_intel("MSFT", "business_summary", fetcher=fetcher, force=True)

    assert counts["n"] == 2
    assert forced.cached is False
    assert forced.content["summary"] == "call 2"


def test_risk_diff_requires_prior_year(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))  # no prior
    svc.set_llm_for_tests(lambda **_kw: {"new_risks": [], "expanded_risks": [], "overall_tone": "unchanged", "ongoing_risks": []})
    with pytest.raises(FilingNotFound):
        svc.get_intel("MSFT", "risk_diff", fetcher=fetcher)


def test_risk_diff_includes_both_accessions_in_key(cache: CacheStub) -> None:
    fetcher = FakeFetcher(
        latest_10k=_bundle("0000-25-001"),
        prior_10k=_bundle("0000-24-001"),
    )
    svc.set_llm_for_tests(lambda **_kw: {"new_risks": [], "expanded_risks": [], "overall_tone": "modestly worse", "ongoing_risks": []})
    r = svc.get_intel("MSFT", "risk_diff", fetcher=fetcher)
    assert r.cache_key == "v3_0000-25-001_vs_0000-24-001"
    assert len(r.sources) == 2


def test_mda_summary_prefers_10q(cache: CacheStub) -> None:
    fetcher = FakeFetcher(
        latest_10k=_bundle("0000-25-001"),
        q10_mda=(_ref("10-Q", "0000-25-Q3"), "Q3 mda body"),
    )
    svc.set_llm_for_tests(lambda **_kw: {
        "revenue_bridge": "x", "margin_drivers": "x", "liquidity": "x",
        "forward_tone": "cautious", "highlights": [],
    })
    r = svc.get_intel("MSFT", "mda_summary", fetcher=fetcher)
    assert r.cache_key == "0000-25-Q3"
    assert r.sources[0]["form"] == "10-Q"


def test_mda_summary_falls_back_to_10k_when_no_10q(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))
    svc.set_llm_for_tests(lambda **_kw: {
        "revenue_bridge": "x", "margin_drivers": "x", "liquidity": "x",
        "forward_tone": "neutral", "highlights": [],
    })
    r = svc.get_intel("MSFT", "mda_summary", fetcher=fetcher)
    assert r.cache_key == "0000-25-001"
    assert r.sources[0]["form"] == "10-K"


def test_leadership_combines_proxy_and_form4(cache: CacheStub) -> None:
    fetcher = FakeFetcher(
        def14a=(_ref("DEF 14A", "0000-25-PROXY"), "Proxy body"),
        form4=Form4Summary(window_days=180, filings_count=3, most_recent_date="2025-05-01",
                           filings=[_ref("4", "f4-1", "2025-05-01"), _ref("4", "f4-2", "2025-04-15")]),
    )
    svc.set_llm_for_tests(lambda **_kw: {
        "ceo_name": "Jane Doe",
        "ceo_tenure_note": "CEO since 2015",
        "comp_alignment": "performance-linked",
        "comp_summary": "Mostly stock and bonus.",
        "insider_activity_note": "Moderate cadence.",
        "concerns": [],
    })
    r = svc.get_intel("MSFT", "leadership", fetcher=fetcher)
    assert r.cache_key == "0000-25-PROXY_f4_3"
    assert any(s["form"] == "DEF 14A" for s in r.sources)
    assert any(s["form"] == "4" for s in r.sources)


def test_unknown_insight_type_raises(cache: CacheStub) -> None:
    with pytest.raises(InvalidInsightType):
        svc.get_intel("MSFT", "not_a_thing", fetcher=FakeFetcher())


def test_empty_ticker_raises(cache: CacheStub) -> None:
    with pytest.raises(InvalidInsightType):
        svc.get_intel("  ", "business_summary", fetcher=FakeFetcher())


def test_llm_failure_becomes_503(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))

    def boom(**_kw: Any) -> dict[str, Any]:
        raise RuntimeError("openai down")

    svc.set_llm_for_tests(boom)
    with pytest.raises(FilingsIntelUnavailable):
        svc.get_intel("MSFT", "business_summary", fetcher=fetcher)


def test_bear_scaffold_uses_business_plus_risk(cache: CacheStub) -> None:
    fetcher = FakeFetcher(latest_10k=_bundle("0000-25-001"))
    svc.set_llm_for_tests(lambda **_kw: {
        "scenarios": [
            {"title": "Margin collapse", "narrative": "...", "probability_range_pct": "5-15%", "metric_to_watch": "gross margin"},
            {"title": "Regulation", "narrative": "...", "probability_range_pct": "5-10%", "metric_to_watch": "DoJ filings"},
            {"title": "Tech shift", "narrative": "...", "probability_range_pct": "10-20%", "metric_to_watch": "R&D ratio"},
        ],
    })
    r = svc.get_intel("MSFT", "bear_scaffold", fetcher=fetcher)
    assert len(r.content["scenarios"]) == 3
    assert r.cache_key == "0000-25-001"
