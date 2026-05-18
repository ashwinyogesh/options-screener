"""Unit tests for ADR-0023 continuity fields (workers/scorer/scorer.py).

Covers `compute_continuity_fields` — pure function that derives
``stage_streak_days``, ``first_emerged_at``, and ``acs_slope_14d`` from
today's ticker_timeline doc plus prior-day history.

Pinning sys.path before module-level imports follows the existing pattern in
test_scorer.py so the flat worker module layout resolves correctly when
pytest is invoked from the repo root.
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

from scorer import compute_continuity_fields  # noqa: E402


def _hist(*entries: tuple[str, int | None, float | None]) -> list[dict]:
    """Build a history list (newest first) from (bucket_date, stage, acs)."""
    return [
        {"bucket_date": bd, "lifecycle_stage": stage, "acs": acs}
        for bd, stage, acs in entries
    ]


class TestStageStreak:
    def test_single_day_streak_when_no_history(self) -> None:
        result = compute_continuity_fields(
            today_stage=2,
            today_bucket_date="2026-05-18",
            today_acs=55.0,
            history=[],
        )
        assert result.stage_streak_days == 1
        assert result.first_emerged_at == "2026-05-18"

    def test_multi_day_streak_with_consecutive_emerging(self) -> None:
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", 1, 50.0),
            ("2026-05-15", 1, 48.0),
        )
        result = compute_continuity_fields(
            today_stage=3,
            today_bucket_date="2026-05-18",
            today_acs=60.0,
            history=history,
        )
        assert result.stage_streak_days == 4
        assert result.first_emerged_at == "2026-05-15"

    def test_zero_streak_when_today_is_late_stage(self) -> None:
        history = _hist(("2026-05-17", 3, 54.0), ("2026-05-16", 2, 50.0))
        result = compute_continuity_fields(
            today_stage=5,
            today_bucket_date="2026-05-18",
            today_acs=30.0,
            history=history,
        )
        assert result.stage_streak_days == 0
        assert result.first_emerged_at is None

    def test_streak_breaks_on_non_emerging_prior_day(self) -> None:
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", 5, 30.0),  # break
            ("2026-05-15", 2, 48.0),  # ignored — already broke
        )
        result = compute_continuity_fields(
            today_stage=2,
            today_bucket_date="2026-05-18",
            today_acs=55.0,
            history=history,
        )
        assert result.stage_streak_days == 2
        assert result.first_emerged_at == "2026-05-17"

    def test_today_none_carries_forward_from_yesterday(self) -> None:
        """Mid-morning render before the hourly detector run should not zero a streak."""
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", 2, 52.0),
            ("2026-05-15", 1, 50.0),
        )
        result = compute_continuity_fields(
            today_stage=None,
            today_bucket_date="2026-05-18",
            today_acs=55.0,
            history=history,
        )
        # today carries forward as 2 → streak counts today + 3 prior emerging days
        assert result.stage_streak_days == 4
        assert result.first_emerged_at == "2026-05-15"

    def test_mid_streak_none_breaks_streak(self) -> None:
        """Null mid-streak (not leading edge) is a break — only one-step carry-forward."""
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", None, None),  # break
            ("2026-05-15", 2, 48.0),
        )
        result = compute_continuity_fields(
            today_stage=2,
            today_bucket_date="2026-05-18",
            today_acs=55.0,
            history=history,
        )
        assert result.stage_streak_days == 2
        assert result.first_emerged_at == "2026-05-17"


class TestAcsSlope:
    def test_slope_none_when_too_few_samples(self) -> None:
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", 2, 52.0),
            ("2026-05-15", 1, 50.0),
        )
        result = compute_continuity_fields(
            today_stage=2,
            today_bucket_date="2026-05-18",
            today_acs=55.0,
            history=history,
        )
        # 4 samples < 5 minimum
        assert result.acs_slope_14d is None

    def test_positive_slope_for_rising_acs(self) -> None:
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", 2, 52.0),
            ("2026-05-15", 2, 50.0),
            ("2026-05-14", 1, 48.0),
            ("2026-05-13", 1, 46.0),
            ("2026-05-12", 1, 44.0),
        )
        result = compute_continuity_fields(
            today_stage=3,
            today_bucket_date="2026-05-18",
            today_acs=56.0,
            history=history,
        )
        assert result.acs_slope_14d is not None
        assert result.acs_slope_14d == pytest.approx(2.0, abs=0.01)

    def test_negative_slope_for_falling_acs(self) -> None:
        history = _hist(
            ("2026-05-17", 3, 58.0),
            ("2026-05-16", 3, 60.0),
            ("2026-05-15", 2, 62.0),
            ("2026-05-14", 2, 64.0),
            ("2026-05-13", 2, 66.0),
        )
        result = compute_continuity_fields(
            today_stage=3,
            today_bucket_date="2026-05-18",
            today_acs=56.0,
            history=history,
        )
        assert result.acs_slope_14d is not None
        assert result.acs_slope_14d < 0

    def test_slope_skips_none_acs_entries(self) -> None:
        """None ACS values are skipped (not zero-imputed) so they don't pull slope down."""
        history = _hist(
            ("2026-05-17", 2, 54.0),
            ("2026-05-16", 2, None),  # skipped
            ("2026-05-15", 2, 50.0),
            ("2026-05-14", 1, 48.0),
            ("2026-05-13", 1, 46.0),
        )
        result = compute_continuity_fields(
            today_stage=2,
            today_bucket_date="2026-05-18",
            today_acs=56.0,
            history=history,
        )
        # 5 valid samples — slope should be defined and positive
        assert result.acs_slope_14d is not None
        assert result.acs_slope_14d > 0
