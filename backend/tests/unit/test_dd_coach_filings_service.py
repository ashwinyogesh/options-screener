"""Unit tests for services/dd_coach/filings_service.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.dd_coach import filings_service
from services.dd_coach.errors import DDEntryNotFound


@pytest.fixture
def fake_cik_map() -> dict[str, str]:
    return {"MSFT": "0000789019", "NBIS": "0001000000"}


class TestGetFilingLinks:
    def test_returns_links_for_known_ticker(
        self, fake_cik_map: dict[str, str],
    ) -> None:
        with patch(
            "services.dd_coach.filings_service.fundamentals_service._load_cik_map",
            return_value=fake_cik_map,
        ):
            links = filings_service.get_filing_links("msft")

        assert links.ticker == "MSFT"
        assert links.cik == "0000789019"
        assert "CIK=0000789019" in links.all_filings
        assert "type=10-K" in links.latest_10k
        assert "type=10-Q" in links.latest_10q
        assert "type=8-K" in links.latest_8k
        assert "type=DEF+14A" in links.proxy_def14a
        assert "type=4" in links.form4_insider

    def test_unknown_ticker_raises_not_found(
        self, fake_cik_map: dict[str, str],
    ) -> None:
        with patch(
            "services.dd_coach.filings_service.fundamentals_service._load_cik_map",
            return_value=fake_cik_map,
        ):
            with pytest.raises(DDEntryNotFound):
                filings_service.get_filing_links("UNKNOWN")

    def test_to_dict_includes_all_link_fields(
        self, fake_cik_map: dict[str, str],
    ) -> None:
        with patch(
            "services.dd_coach.filings_service.fundamentals_service._load_cik_map",
            return_value=fake_cik_map,
        ):
            d = filings_service.get_filing_links("MSFT").to_dict()

        for key in (
            "ticker", "cik", "all_filings", "latest_10k",
            "latest_10q", "latest_8k", "proxy_def14a", "form4_insider",
        ):
            assert key in d
            assert d[key]
