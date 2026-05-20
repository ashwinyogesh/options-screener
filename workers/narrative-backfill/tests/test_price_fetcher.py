"""Tests for price_fetcher (forward close lookup)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from price_fetcher import (
    ForwardPrices,
    _pick_offsets,
    fetch_forward_prices,
)


class TestForwardPrices:
    def test_is_complete_all_set(self) -> None:
        p = ForwardPrices(t0=1.0, t5=2.0, t10=3.0, t20=4.0)
        assert p.is_complete() is True

    def test_is_complete_with_none(self) -> None:
        assert ForwardPrices(t0=1.0, t5=None, t10=3.0, t20=4.0).is_complete() is False

    def test_as_dict_prefixes(self) -> None:
        p = ForwardPrices(t0=1.0, t5=2.0, t10=3.0, t20=4.0)
        assert p.as_dict("px") == {
            "px_at_signal": 1.0,
            "px_t5": 2.0,
            "px_t10": 3.0,
            "px_t20": 4.0,
        }
        assert p.as_dict("spy")["spy_t20"] == 4.0


class TestPickOffsets:
    def test_full_series(self) -> None:
        closes = [100 + i for i in range(25)]  # 25 trading days
        prices = _pick_offsets(closes)
        assert prices.t0 == 100
        assert prices.t5 == 105
        assert prices.t10 == 110
        assert prices.t20 == 120

    def test_partial_series_returns_none_for_missing_offsets(self) -> None:
        closes = [100, 101, 102, 103]  # only 4 days available — T+5/T+10/T+20 missing
        prices = _pick_offsets(closes)
        assert prices.t0 == 100
        assert prices.t5 is None
        assert prices.t10 is None
        assert prices.t20 is None

    def test_empty_series_returns_all_none(self) -> None:
        prices = _pick_offsets([])
        assert prices.t0 is None
        assert prices.t5 is None
        assert prices.t10 is None
        assert prices.t20 is None

    def test_exactly_enough_for_t5(self) -> None:
        closes = [100, 101, 102, 103, 104, 105]  # 6 days → T+5 lands on last
        prices = _pick_offsets(closes)
        assert prices.t5 == 105
        assert prices.t10 is None


class TestFetchForwardPrices:
    """End-to-end with yfinance mocked at the import site."""

    def _hist(self, closes: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"Close": closes})

    def test_full_window_returns_all_offsets(self) -> None:
        closes = [100 + i for i in range(25)]
        fake_yf = MagicMock()
        fake_yf.download.return_value = self._hist(closes)
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            prices = fetch_forward_prices(
                "AAPL", "2026-04-01", today=date(2026, 5, 19),
            )
        assert prices.t0 == 100
        assert prices.t5 == 105
        assert prices.t20 == 120

    def test_future_event_date_returns_all_none(self) -> None:
        """An event_date after today has nothing to fetch."""
        fake_yf = MagicMock()
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            prices = fetch_forward_prices(
                "AAPL", "2026-06-01", today=date(2026, 5, 19),
            )
        assert prices == ForwardPrices(None, None, None, None)
        fake_yf.download.assert_not_called()

    def test_invalid_event_date_returns_all_none(self) -> None:
        prices = fetch_forward_prices(
            "AAPL", "not-a-date", today=date(2026, 5, 19),
        )
        assert prices == ForwardPrices(None, None, None, None)

    def test_yfinance_download_failure_is_caught(self) -> None:
        fake_yf = MagicMock()
        fake_yf.download.side_effect = RuntimeError("yfinance down")
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            prices = fetch_forward_prices(
                "AAPL", "2026-04-01", today=date(2026, 5, 19),
            )
        assert prices == ForwardPrices(None, None, None, None)

    def test_empty_history_returns_all_none(self) -> None:
        fake_yf = MagicMock()
        fake_yf.download.return_value = pd.DataFrame()
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            prices = fetch_forward_prices(
                "AAPL", "2026-04-01", today=date(2026, 5, 19),
            )
        assert prices == ForwardPrices(None, None, None, None)

    def test_partial_history_yields_partial_prices(self) -> None:
        """8 trading days back → T+0 + T+5 set, T+10 and T+20 null."""
        closes = [100 + i for i in range(8)]
        fake_yf = MagicMock()
        fake_yf.download.return_value = self._hist(closes)
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            prices = fetch_forward_prices(
                "AAPL", "2026-05-08", today=date(2026, 5, 19),
            )
        assert prices.t0 == 100
        assert prices.t5 == 105
        assert prices.t10 is None
        assert prices.t20 is None
