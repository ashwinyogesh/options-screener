"""Unit tests for detect_alerts() (workers/scorer/scorer.py, Phase 7).

Covers all three alert types:
    stage_2_entry   — fires on stage transition into 2; silent when already 2
    stage_3_entry   — fires on stage transition into 3; silent when already 3
    acs_rising_fast — fires on ≥15pt delta with a real prior; silent with no
                      history, an unscored prior (acs=0), or a sub-threshold delta

sys.path pinning follows the pattern in test_scorer.py and test_continuity.py so
the flat worker module layout resolves correctly when pytest is invoked from the
repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
if _WORKER_ROOT in sys.path:
    sys.path.remove(_WORKER_ROOT)
sys.path.insert(0, _WORKER_ROOT)
for _name in ("main", "config", "cosmos_client", "kv_secrets", "scorer"):
    sys.modules.pop(_name, None)

from scorer import detect_alerts  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hist(stage: int | None, acs: float) -> list[dict]:
    """Single-element history list (newest first) with stage and acs."""
    return [{"lifecycle_stage": stage, "acs": acs, "bucket_date": "2026-05-18"}]


def _alert_types(alerts: list[dict]) -> list[str]:
    return [a["alert_type"] for a in alerts]


# ---------------------------------------------------------------------------
# stage_2_entry
# ---------------------------------------------------------------------------

class TestStage2Entry:
    def test_fires_on_transition_into_stage_2(self) -> None:
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=2,
            today_acs=55.0,
            bucket_date="2026-05-19",
            history=_hist(stage=1, acs=48.0),
        )
        assert "stage_2_entry" in _alert_types(alerts)

    def test_silent_when_already_stage_2(self) -> None:
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=2,
            today_acs=57.0,
            bucket_date="2026-05-19",
            history=_hist(stage=2, acs=55.0),
        )
        assert "stage_2_entry" not in _alert_types(alerts)

    def test_fires_when_returning_to_stage_2_from_higher(self) -> None:
        # stage 4 → 2 is unusual but the alert should still fire
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=2,
            today_acs=52.0,
            bucket_date="2026-05-19",
            history=_hist(stage=4, acs=60.0),
        )
        assert "stage_2_entry" in _alert_types(alerts)

    def test_fires_with_empty_history(self) -> None:
        # No prior: prior_stage defaults to None → fires because None != 2
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=2,
            today_acs=50.0,
            bucket_date="2026-05-19",
            history=[],
        )
        assert "stage_2_entry" in _alert_types(alerts)

    def test_alert_id_is_idempotency_key(self) -> None:
        alerts = detect_alerts(
            ticker="AAPL",
            today_stage=2,
            today_acs=50.0,
            bucket_date="2026-05-19",
            history=_hist(stage=1, acs=45.0),
        )
        entry = next(a for a in alerts if a["alert_type"] == "stage_2_entry")
        assert entry["id"] == "AAPL_stage_2_entry_2026-05-19"


# ---------------------------------------------------------------------------
# stage_3_entry
# ---------------------------------------------------------------------------

class TestStage3Entry:
    def test_fires_on_transition_into_stage_3(self) -> None:
        alerts = detect_alerts(
            ticker="GOOGL",
            today_stage=3,
            today_acs=70.0,
            bucket_date="2026-05-19",
            history=_hist(stage=2, acs=60.0),
        )
        assert "stage_3_entry" in _alert_types(alerts)

    def test_silent_when_already_stage_3(self) -> None:
        alerts = detect_alerts(
            ticker="GOOGL",
            today_stage=3,
            today_acs=72.0,
            bucket_date="2026-05-19",
            history=_hist(stage=3, acs=70.0),
        )
        assert "stage_3_entry" not in _alert_types(alerts)

    def test_stage_3_alert_id(self) -> None:
        alerts = detect_alerts(
            ticker="MSFT",
            today_stage=3,
            today_acs=68.0,
            bucket_date="2026-05-19",
            history=_hist(stage=2, acs=55.0),
        )
        entry = next(a for a in alerts if a["alert_type"] == "stage_3_entry")
        assert entry["id"] == "MSFT_stage_3_entry_2026-05-19"


# ---------------------------------------------------------------------------
# acs_rising_fast
# ---------------------------------------------------------------------------

class TestAcsRisingFast:
    def test_fires_on_delta_at_threshold(self) -> None:
        # Exactly at threshold (15.0)
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=3,
            today_acs=55.0,
            bucket_date="2026-05-19",
            history=_hist(stage=3, acs=40.0),
        )
        assert "acs_rising_fast" in _alert_types(alerts)

    def test_fires_on_delta_above_threshold(self) -> None:
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=3,
            today_acs=75.0,
            bucket_date="2026-05-19",
            history=_hist(stage=3, acs=50.0),
        )
        assert "acs_rising_fast" in _alert_types(alerts)

    def test_silent_on_delta_below_threshold(self) -> None:
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=3,
            today_acs=53.0,
            bucket_date="2026-05-19",
            history=_hist(stage=3, acs=40.0),  # delta = 13 < 15
        )
        assert "acs_rising_fast" not in _alert_types(alerts)

    def test_silent_with_no_history(self) -> None:
        # No prior doc at all — should never fire a spike alert
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=3,
            today_acs=80.0,
            bucket_date="2026-05-19",
            history=[],
        )
        assert "acs_rising_fast" not in _alert_types(alerts)

    def test_silent_when_prior_acs_is_zero(self) -> None:
        # Prior doc exists but was never scored (acs=0) — first real scoring
        # run should not trigger a false spike. Validates today's fix.
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=2,
            today_acs=50.0,
            bucket_date="2026-05-19",
            history=_hist(stage=1, acs=0.0),
        )
        assert "acs_rising_fast" not in _alert_types(alerts)

    def test_silent_when_prior_acs_is_none(self) -> None:
        # Prior doc has no acs field — same treatment as acs=0
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=2,
            today_acs=50.0,
            bucket_date="2026-05-19",
            history=[{"lifecycle_stage": 1, "acs": None, "bucket_date": "2026-05-18"}],
        )
        assert "acs_rising_fast" not in _alert_types(alerts)

    def test_alert_payload_contains_delta(self) -> None:
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=3,
            today_acs=65.0,
            bucket_date="2026-05-19",
            history=_hist(stage=3, acs=45.0),
        )
        entry = next(a for a in alerts if a["alert_type"] == "acs_rising_fast")
        assert entry["payload"]["delta"] == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# No alerts for non-emerging stages
# ---------------------------------------------------------------------------

class TestNoAlertsNonEmergingStage:
    def test_no_stage_entry_alert_for_stage_4(self) -> None:
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=4,
            today_acs=60.0,
            bucket_date="2026-05-19",
            history=_hist(stage=3, acs=55.0),
        )
        assert "stage_2_entry" not in _alert_types(alerts)
        assert "stage_3_entry" not in _alert_types(alerts)

    def test_spike_still_fires_for_stage_4_if_delta_large(self) -> None:
        # The acs_rising_fast alert is stage-agnostic — it fires regardless of stage
        alerts = detect_alerts(
            ticker="NVDA",
            today_stage=4,
            today_acs=70.0,
            bucket_date="2026-05-19",
            history=_hist(stage=4, acs=50.0),
        )
        assert "acs_rising_fast" in _alert_types(alerts)
