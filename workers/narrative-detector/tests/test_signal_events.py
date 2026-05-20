"""Tests for the forward signal log (signal_events).

Covers two surfaces:

* ``main._build_signal_event`` — pure shaping logic that turns an in-flight
  transition into a Cosmos doc.  No I/O, no patching needed.
* ``DetectorCosmosClient.write_signal_event`` — minimal validation guard.
  Cosmos itself is mocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from detector import ClusterResult
from main import _build_signal_event
from smoothing import LifecycleState


def _cluster(
    n_embedded: int = 10,
    n_clusters: int = 1,
    dominant_fraction: float = 0.85,
) -> ClusterResult:
    return ClusterResult(
        labels=[0] * n_embedded,
        n_clusters=n_clusters,
        dominant_cluster=0 if n_clusters else -1,
        dominant_fraction=dominant_fraction,
        n_embedded=n_embedded,
    )


class TestBuildSignalEvent:
    def test_deterministic_id_for_same_hour(self) -> None:
        """Same ticker + same hour + same transition → identical id (idempotent)."""
        dt = datetime(2026, 5, 19, 14, 30, 0, tzinfo=timezone.utc)
        ev_a = _build_signal_event(
            ticker="AAPL",
            event_dt=dt,
            prev_stage=2,
            new_stage=3,
            confidence=0.78,
            cluster_result=_cluster(),
            prior_state=LifecycleState(),
            new_state=LifecycleState(),
        )
        ev_b = _build_signal_event(
            ticker="AAPL",
            event_dt=dt.replace(minute=45),  # same hour, different minute
            prev_stage=2,
            new_stage=3,
            confidence=0.81,  # confidence may shift; id must not
            cluster_result=_cluster(),
            prior_state=LifecycleState(),
            new_state=LifecycleState(),
        )
        assert ev_a["id"] == ev_b["id"] == "AAPL_2026-05-19_h14_stage2to3"

    def test_id_distinguishes_transition_direction(self) -> None:
        dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        up = _build_signal_event(
            ticker="AAPL", event_dt=dt, prev_stage=2, new_stage=3,
            confidence=0.5, cluster_result=_cluster(),
            prior_state=LifecycleState(), new_state=LifecycleState(),
        )
        down = _build_signal_event(
            ticker="AAPL", event_dt=dt, prev_stage=3, new_stage=2,
            confidence=0.5, cluster_result=_cluster(),
            prior_state=LifecycleState(), new_state=LifecycleState(),
        )
        assert up["id"] != down["id"]

    def test_breadth_delta_reflects_movement(self) -> None:
        dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        prior = LifecycleState(
            smoothed_inputs={
                "tier1_pct": 0.10,
                "contributor_count_growth_7d": 0.10,
                "dd_post_ratio": 0.05,
            },
        )
        new = LifecycleState(
            smoothed_inputs={
                "tier1_pct": 0.40,
                "contributor_count_growth_7d": 0.30,
                "dd_post_ratio": 0.20,
            },
        )
        event = _build_signal_event(
            ticker="AAPL", event_dt=dt, prev_stage=1, new_stage=2,
            confidence=0.6, cluster_result=_cluster(),
            prior_state=prior, new_state=new,
        )
        assert event["breadth_score"] > event["prior_breadth_score"]
        assert event["breadth_delta"] == pytest.approx(
            event["breadth_score"] - event["prior_breadth_score"], rel=1e-6,
        )

    def test_price_fields_are_null_placeholders(self) -> None:
        """All price columns must be present and null — backfill job populates them."""
        dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        event = _build_signal_event(
            ticker="AAPL", event_dt=dt, prev_stage=1, new_stage=2,
            confidence=0.6, cluster_result=_cluster(),
            prior_state=LifecycleState(), new_state=LifecycleState(),
        )
        for field in (
            "px_at_signal", "spy_at_signal",
            "px_t5", "spy_t5",
            "px_t10", "spy_t10",
            "px_t20", "spy_t20",
            "backfilled_at",
        ):
            assert field in event
            assert event[field] is None

    def test_cluster_diagnostics_round_tripped(self) -> None:
        dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        result = _cluster(n_embedded=15, n_clusters=0, dominant_fraction=0.0)
        event = _build_signal_event(
            ticker="GOOGL", event_dt=dt, prev_stage=0, new_stage=2,
            confidence=0.45, cluster_result=result,
            prior_state=LifecycleState(), new_state=LifecycleState(),
        )
        assert event["n_embedded"] == 15
        assert event["n_clusters"] == 0
        assert event["dominant_fraction"] == 0.0

    def test_cold_start_transition_recorded(self) -> None:
        """prev_stage=0 → new_stage=2 still produces a valid event (cold start)."""
        dt = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        event = _build_signal_event(
            ticker="NVDA", event_dt=dt, prev_stage=0, new_stage=2,
            confidence=0.55, cluster_result=_cluster(),
            prior_state=LifecycleState(), new_state=LifecycleState(),
        )
        assert event["prev_stage"] == 0
        assert event["new_stage"] == 2
        assert event["id"] == "NVDA_2026-05-19_h14_stage0to2"

    def test_event_ts_is_iso_utc(self) -> None:
        dt = datetime(2026, 5, 19, 14, 5, 30, tzinfo=timezone.utc)
        event = _build_signal_event(
            ticker="AAPL", event_dt=dt, prev_stage=1, new_stage=2,
            confidence=0.5, cluster_result=_cluster(),
            prior_state=LifecycleState(), new_state=LifecycleState(),
        )
        assert event["event_ts"] == "2026-05-19T14:05:30Z"
        assert event["event_date"] == "2026-05-19"


class TestWriteSignalEvent:
    """The cosmos_client.write_signal_event method — validation + I/O dispatch."""

    def _make_client(self) -> tuple[object, MagicMock]:
        """Build a DetectorCosmosClient with mocked Cosmos containers."""
        from cosmos_client import DetectorCosmosClient

        client = DetectorCosmosClient.__new__(DetectorCosmosClient)
        client._signal_events = MagicMock()  # type: ignore[attr-defined]
        return client, client._signal_events  # type: ignore[attr-defined]

    def test_upsert_called_with_doc(self) -> None:
        client, container = self._make_client()
        event = {
            "id": "AAPL_2026-05-19_h14_stage2to3",
            "ticker": "AAPL",
            "event_date": "2026-05-19",
            "prev_stage": 2,
            "new_stage": 3,
        }
        client.write_signal_event(event)
        container.upsert_item.assert_called_once_with(event)

    def test_missing_required_field_raises(self) -> None:
        client, container = self._make_client()
        with pytest.raises(ValueError, match="prev_stage"):
            client.write_signal_event({
                "id": "x",
                "ticker": "AAPL",
                "event_date": "2026-05-19",
                "new_stage": 3,
            })
        container.upsert_item.assert_not_called()
