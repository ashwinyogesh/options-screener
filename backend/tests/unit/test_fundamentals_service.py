"""Unit tests for `services.fundamentals_service`.

Validates the cache + fetcher orchestration with stubbed components — no
network calls, no real disk I/O outside `tmp_path`.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from services import fundamentals_service as fs
from services.edgar import DiskFundamentalsCache, EdgarUnavailable
from tests.fixtures.edgar import make_facts


class _StubFetcher:
    """Replaces `EdgarFetcher`; returns canned payloads or simulated failures."""

    def __init__(
        self,
        cik_map: dict[str, str] | None = None,
        facts_by_cik: dict[str, dict] | None = None,
        fail_cik_map: bool = False,
        fail_companyfacts: bool = False,
    ) -> None:
        self.cik_map = cik_map or {}
        self.facts_by_cik = facts_by_cik or {}
        self.fail_cik_map = fail_cik_map
        self.fail_companyfacts = fail_companyfacts
        self.cik_calls = 0
        self.facts_calls: list[str] = []

    def fetch_cik_map(self) -> dict[str, str]:
        self.cik_calls += 1
        if self.fail_cik_map:
            raise EdgarUnavailable("simulated cik map failure")
        return dict(self.cik_map)

    def fetch_companyfacts(self, cik10: str) -> dict | None:
        self.facts_calls.append(cik10)
        if self.fail_companyfacts:
            raise EdgarUnavailable("simulated companyfacts failure")
        return self.facts_by_cik.get(cik10)


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    """Each test gets a clean module-level state."""
    fs._cache = None
    fs._fetcher = None
    fs._cik_map = None
    yield
    fs._cache = None
    fs._fetcher = None
    fs._cik_map = None


@pytest.fixture
def disk_cache(tmp_path: Path) -> DiskFundamentalsCache:
    return DiskFundamentalsCache(tmp_path / "edgar")


# ---------------------------------------------------------------------------
# Happy path: lazy fetch on cache miss
# ---------------------------------------------------------------------------

def test_get_pit_factors_lazy_fetches_on_cache_miss(disk_cache: DiskFundamentalsCache) -> None:
    facts = make_facts()
    fetcher = _StubFetcher(
        cik_map={"TEST": "0001234567"},
        facts_by_cik={"0001234567": facts},
    )
    fs.configure(cache=disk_cache, fetcher=fetcher)

    out = fs.get_pit_factors("TEST", date(2024, 6, 30), spot_price=50.0)

    assert out["ps_ttm"] == pytest.approx(0.5)
    assert fetcher.cik_calls == 1
    assert fetcher.facts_calls == ["0001234567"]


def test_subsequent_calls_use_cache(disk_cache: DiskFundamentalsCache) -> None:
    facts = make_facts()
    fetcher = _StubFetcher(
        cik_map={"TEST": "0001234567"},
        facts_by_cik={"0001234567": facts},
    )
    fs.configure(cache=disk_cache, fetcher=fetcher)

    fs.get_pit_factors("TEST", date(2024, 6, 30), spot_price=50.0)
    fs.get_pit_factors("TEST", date(2024, 7, 31), spot_price=55.0)
    fs.get_pit_factors("TEST", date(2024, 8, 31), spot_price=60.0)

    # Only the first call should hit the network.
    assert fetcher.facts_calls == ["0001234567"]


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_unknown_ticker_returns_all_none(disk_cache: DiskFundamentalsCache) -> None:
    fetcher = _StubFetcher(cik_map={"OTHER": "0009999999"}, facts_by_cik={})
    fs.configure(cache=disk_cache, fetcher=fetcher)

    out = fs.get_pit_factors("UNKNOWN", date(2024, 6, 30), spot_price=50.0)

    for v in out.values():
        assert v is None


def test_cik_map_failure_yields_empty_factors(disk_cache: DiskFundamentalsCache) -> None:
    fetcher = _StubFetcher(fail_cik_map=True)
    fs.configure(cache=disk_cache, fetcher=fetcher)

    out = fs.get_pit_factors("TEST", date(2024, 6, 30), spot_price=50.0)

    for v in out.values():
        assert v is None


def test_companyfacts_fetch_failure_falls_back_to_stale_cache(
    disk_cache: DiskFundamentalsCache,
) -> None:
    facts = make_facts()
    # Pre-populate the cache.
    disk_cache.put_companyfacts("TEST", facts)
    # Force a refresh window that will trigger a network call.
    fs.configure(
        cache=disk_cache,
        fetcher=_StubFetcher(
            cik_map={"TEST": "0001234567"},
            fail_companyfacts=True,
        ),
        refresh_after_days=0.0,
    )

    out = fs.get_pit_factors("TEST", date(2024, 6, 30), spot_price=50.0)

    # Stale-but-cached values should still come through.
    assert out["ps_ttm"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Lag accessor
# ---------------------------------------------------------------------------

def test_get_pit_factors_with_lag(disk_cache: DiskFundamentalsCache) -> None:
    facts = make_facts()
    fetcher = _StubFetcher(
        cik_map={"TEST": "0001234567"},
        facts_by_cik={"0001234567": facts},
    )
    fs.configure(cache=disk_cache, fetcher=fetcher)

    factors, lag = fs.get_pit_factors_with_lag("TEST", date(2024, 6, 30), spot_price=50.0)

    assert factors["ps_ttm"] == pytest.approx(0.5)
    assert lag == (date(2024, 6, 30) - date(2024, 2, 15)).days


# ---------------------------------------------------------------------------
# prefetch helper
# ---------------------------------------------------------------------------

def test_prefetch_reports_per_ticker_status(disk_cache: DiskFundamentalsCache) -> None:
    facts = make_facts()
    fetcher = _StubFetcher(
        cik_map={"TEST": "0001234567", "OTHER": "0009999999"},
        facts_by_cik={"0001234567": facts},
    )
    fs.configure(cache=disk_cache, fetcher=fetcher)

    statuses = fs.prefetch(["TEST", "OTHER", "UNKNOWN"])

    assert statuses["TEST"] == "ok"
    assert statuses["OTHER"] == "no_cik"   # 404 from companyfacts
    assert statuses["UNKNOWN"] == "no_cik"  # not in CIK map
