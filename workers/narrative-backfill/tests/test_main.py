"""End-to-end test for the backfill main() orchestrator (with all I/O mocked)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import main as backfill_main
from price_fetcher import ForwardPrices


class TestMain:
    def _events(self) -> list[dict]:
        return [
            {
                "id": "AAPL_2026-04-01_h14_stage2to3",
                "ticker": "AAPL",
                "event_date": "2026-04-01",
            },
            {
                "id": "GOOGL_2026-05-08_h14_stage1to2",
                "ticker": "GOOGL",
                "event_date": "2026-05-08",
            },
        ]

    def test_full_run_marks_completed_events(self) -> None:
        """Both events fully fillable → both marked complete."""
        full = ForwardPrices(t0=100, t5=105, t10=110, t20=120)

        cosmos = MagicMock()
        cosmos.fetch_unfilled_events.return_value = self._events()

        with patch.object(backfill_main, "BackfillCosmosClient", return_value=cosmos), \
             patch.object(backfill_main, "fetch_forward_prices", return_value=full), \
             patch.object(backfill_main, "load_from_env", return_value=_fake_config()):
            backfill_main.main()

        # update called twice; both with mark_complete=True
        assert cosmos.update_event_prices.call_count == 2
        for call in cosmos.update_event_prices.call_args_list:
            assert call.kwargs["mark_complete"] is True

    def test_partial_fill_does_not_mark_complete(self) -> None:
        partial = ForwardPrices(t0=100, t5=105, t10=None, t20=None)

        cosmos = MagicMock()
        cosmos.fetch_unfilled_events.return_value = [self._events()[1]]  # the recent one

        with patch.object(backfill_main, "BackfillCosmosClient", return_value=cosmos), \
             patch.object(backfill_main, "fetch_forward_prices", return_value=partial), \
             patch.object(backfill_main, "load_from_env", return_value=_fake_config()):
            backfill_main.main()

        cosmos.update_event_prices.assert_called_once()
        assert cosmos.update_event_prices.call_args.kwargs["mark_complete"] is False

    def test_per_event_failure_does_not_abort_run(self) -> None:
        full = ForwardPrices(t0=100, t5=105, t10=110, t20=120)
        cosmos = MagicMock()
        cosmos.fetch_unfilled_events.return_value = self._events()
        # First update raises, second succeeds.
        cosmos.update_event_prices.side_effect = [RuntimeError("cosmos blip"), None]

        with patch.object(backfill_main, "BackfillCosmosClient", return_value=cosmos), \
             patch.object(backfill_main, "fetch_forward_prices", return_value=full), \
             patch.object(backfill_main, "load_from_env", return_value=_fake_config()):
            backfill_main.main()  # must not raise

        assert cosmos.update_event_prices.call_count == 2

    def test_empty_queue_is_a_clean_noop(self) -> None:
        cosmos = MagicMock()
        cosmos.fetch_unfilled_events.return_value = []

        with patch.object(backfill_main, "BackfillCosmosClient", return_value=cosmos), \
             patch.object(backfill_main, "fetch_forward_prices") as mock_fetch, \
             patch.object(backfill_main, "load_from_env", return_value=_fake_config()):
            backfill_main.main()

        mock_fetch.assert_not_called()
        cosmos.update_event_prices.assert_not_called()

    def test_malformed_event_is_skipped(self) -> None:
        """Missing ticker or event_date → log + skip, don't crash."""
        cosmos = MagicMock()
        cosmos.fetch_unfilled_events.return_value = [
            {"id": "broken"},  # no ticker, no event_date
        ]

        with patch.object(backfill_main, "BackfillCosmosClient", return_value=cosmos), \
             patch.object(backfill_main, "fetch_forward_prices") as mock_fetch, \
             patch.object(backfill_main, "load_from_env", return_value=_fake_config()):
            backfill_main.main()

        mock_fetch.assert_not_called()
        cosmos.update_event_prices.assert_not_called()


def _fake_config() -> object:
    from config import BackfillConfig
    return BackfillConfig(
        keyvault_uri="https://kv-fake.vault.azure.net/",
        cosmos_endpoint="https://cosmos-fake.documents.azure.com:443/",
        cosmos_db="narrative",
        log_level="WARNING",
        max_events=10,
        benchmark_ticker="SPY",
    )
