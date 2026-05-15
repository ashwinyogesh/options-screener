"""Unit tests for services.narrative.attention pure functions.

Probes:
- decay_weighted_density: zero/single/full-window, decay ordering.
- compute_acceleration: positive, negative, zero-baseline.
- compute_gini: equal distribution, full concentration, empty.
- compute_financial_term_density: with/without terms.
- compute_dd_post_ratio: flair-matching and body-matching.
- build_snapshot: end-to-end with synthetic 14-day signal set.

All expected values are hand-computed so that calibration drift in the
constants (λ, weights, thresholds) is caught at the unit level.
No I/O — pure function tests only.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from services.narrative.attention import (
    build_snapshot,
    compute_acceleration,
    compute_attention_quality,
    compute_dd_post_ratio,
    compute_financial_term_density,
    compute_gini,
    decay_weighted_density,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# decay_weighted_density
# ---------------------------------------------------------------------------

class TestDecayWeightedDensity:
    def test_empty_returns_zero(self) -> None:
        assert decay_weighted_density([], date(2026, 5, 1), 7) == 0.0

    def test_single_day_today_returns_fraction(self) -> None:
        anchor = date(2026, 5, 1)
        # One signal on the reference date: result = e^0 / max_weight_7d.
        # max_weight_7d = sum(e^(-0.1*t) for t=0..7) > 1, so result < 1.
        result = decay_weighted_density([(anchor, 1)], anchor, 7)
        lam = 0.1
        max_w = sum(math.exp(-lam * t) for t in range(8))
        assert result == pytest.approx(1.0 / max_w, rel=1e-6)
        assert 0.0 < result < 1.0

    def test_outside_window_ignored(self) -> None:
        anchor = date(2026, 5, 1)
        old = anchor - timedelta(days=10)
        result = decay_weighted_density([(old, 100)], anchor, 7)
        assert result == 0.0

    def test_older_day_lower_weight(self) -> None:
        # With a single data point, decay_weighted_density normalises by that
        # point's own weight → always 1.0 regardless of how old it is.
        # Use two-point windows to expose the ordering effect.
        anchor = date(2026, 5, 7)
        # Window covering today + 6 days ago; count only on today vs only 6-days-ago.
        today_pair = [(anchor, 1), (anchor - timedelta(days=6), 0)]
        old_pair = [(anchor, 0), (anchor - timedelta(days=6), 1)]
        d_today = decay_weighted_density(today_pair, anchor, 7)
        d_old = decay_weighted_density(old_pair, anchor, 7)
        assert d_today > d_old

    def test_normalized_to_at_most_one(self) -> None:
        anchor = date(2026, 5, 1)
        # 7 days each with 1000 mentions; result must be ≤ 1.
        pairs = [(anchor - timedelta(days=i), 1000) for i in range(7)]
        result = decay_weighted_density(pairs, anchor, 7)
        assert result <= 1.0

    def test_lambda_decay_math(self) -> None:
        """Spot-check against hand-computed value (λ=0.1, window=7)."""
        anchor = date(2026, 5, 2)
        yesterday = anchor - timedelta(days=1)
        # Two signals: today (t=0) and yesterday (t=1).
        lam = 0.1
        w0 = math.exp(-lam * 0)  # 1.0
        w1 = math.exp(-lam * 1)  # e^-0.1
        # With corrected normalization: denominator = max_weight = sum over full window.
        max_w = sum(math.exp(-lam * t) for t in range(8))  # window_days=7, t=0..7
        expected = (w0 * 1 + w1 * 1) / max_w
        pairs = [(anchor, 1), (yesterday, 1)]
        result = decay_weighted_density(pairs, anchor, 7)
        assert result == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# compute_acceleration
# ---------------------------------------------------------------------------

class TestComputeAcceleration:
    def test_zero_baseline_returns_zero(self) -> None:
        assert compute_acceleration(0.5, 0.0) == 0.0

    def test_positive_acceleration(self) -> None:
        # 7d > 30d → positive
        result = compute_acceleration(0.6, 0.4)
        assert result == pytest.approx((0.6 - 0.4) / 0.4)
        assert result > 0

    def test_negative_acceleration(self) -> None:
        result = compute_acceleration(0.2, 0.5)
        assert result < 0

    def test_equal_densities(self) -> None:
        assert compute_acceleration(0.4, 0.4) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_gini
# ---------------------------------------------------------------------------

class TestComputeGini:
    def test_empty_returns_zero(self) -> None:
        assert compute_gini([]) == 0.0

    def test_single_author_returns_zero(self) -> None:
        assert compute_gini([10]) == 0.0

    def test_equal_distribution_near_zero(self) -> None:
        # 5 authors, each 2 mentions → Gini ≈ 0
        result = compute_gini([2, 2, 2, 2, 2])
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_full_concentration_near_one(self) -> None:
        # One author has all mentions; the rest have 1 each.
        # Actual Gini([1,1,1,1,1000]) ≈ 0.796 — well above the §2.3 flag
        # threshold of 0.65, so this is the correct boundary to assert.
        counts = [1, 1, 1, 1, 1000]
        result = compute_gini(counts)
        assert result > 0.65  # above concentration-flag threshold per §2.3

    def test_moderate_concentration_between(self) -> None:
        result = compute_gini([1, 2, 3, 4, 10])
        assert 0.0 < result < 1.0

    def test_result_in_unit_interval(self) -> None:
        import random
        rng = random.Random(42)
        counts = [rng.randint(1, 50) for _ in range(20)]
        result = compute_gini(counts)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# compute_financial_term_density
# ---------------------------------------------------------------------------

class TestComputeFinancialTermDensity:
    def test_empty_returns_zero(self) -> None:
        assert compute_financial_term_density([]) == 0.0

    def test_no_financial_terms(self) -> None:
        result = compute_financial_term_density(["I like pizza and coffee today"])
        assert result == 0.0

    def test_dense_financial_post(self) -> None:
        body = "earnings eps revenue guidance dcf valuation bull bear thesis"
        result = compute_financial_term_density([body])
        assert result > 0.0

    def test_mixed_posts_average(self) -> None:
        dense = "earnings eps revenue guidance dcf"
        empty = "I like pizza and coffee"
        d_dense = compute_financial_term_density([dense])
        d_both = compute_financial_term_density([dense, empty])
        # Average of dense + empty should be less than dense alone.
        assert d_both < d_dense

    def test_result_capped_at_one(self) -> None:
        # A post that is 100% financial terms should still return ≤ 1.
        # Use a single very short body with exactly one term = 1 token.
        result = compute_financial_term_density(["earnings"])
        assert result <= 1.0


# ---------------------------------------------------------------------------
# compute_dd_post_ratio
# ---------------------------------------------------------------------------

class TestComputeDdPostRatio:
    def test_empty_returns_zero(self) -> None:
        assert compute_dd_post_ratio([], []) == 0.0

    def test_flair_match(self) -> None:
        result = compute_dd_post_ratio(["DD"], ["some body text"])
        assert result == pytest.approx(1.0)

    def test_body_match(self) -> None:
        result = compute_dd_post_ratio([None], ["This is my bull case for NVDA"])
        assert result == pytest.approx(1.0)

    def test_no_match(self) -> None:
        result = compute_dd_post_ratio(["News"], ["Company reported earnings today"])
        assert result == 0.0

    def test_half_match(self) -> None:
        result = compute_dd_post_ratio(
            ["DD", "News"],
            ["deep dive writeup here", "Company reported earnings today"],
        )
        assert result == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compute_attention_quality
# ---------------------------------------------------------------------------

class TestComputeAttentionQuality:
    def test_all_zeros(self) -> None:
        assert compute_attention_quality(0.0, 0.0, 0.0, 0.0) == 0.0

    def test_all_ones(self) -> None:
        assert compute_attention_quality(1.0, 1.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_negative_acceleration_clipped_to_zero(self) -> None:
        # Negative acceleration contributes 0 to quality score.
        with_neg = compute_attention_quality(0.5, 0.5, 0.5, -1.0)
        with_zero = compute_attention_quality(0.5, 0.5, 0.5, 0.0)
        assert with_neg == pytest.approx(with_zero)

    def test_weights_sum_to_one(self) -> None:
        """With all inputs = 1, result = sum of weights = 1.0."""
        result = compute_attention_quality(1.0, 1.0, 1.0, 1.0)
        assert result == pytest.approx(1.0, rel=1e-9)


# ---------------------------------------------------------------------------
# _normalize_for_quality
# ---------------------------------------------------------------------------

class TestNormalizeForQuality:
    def test_returns_unit_interval(self) -> None:
        from services.narrative.attention import _normalize_for_quality

        p, d, depth, a = _normalize_for_quality(
            dwd_14d=0.6,
            unique_authors_14d=10,
            gini_14d=0.4,
            financial_term_density=0.2,
            dd_post_ratio=0.3,
            acceleration_7d=0.25,
        )
        assert 0.0 <= p <= 1.0
        assert 0.0 <= d <= 1.0
        assert 0.0 <= depth <= 1.0
        assert 0.0 <= a <= 1.0
        # Persistence is the raw dwd, capped.
        assert p == pytest.approx(0.6)
        # Diversity = (10/20) * (1 - 0.4) = 0.3
        assert d == pytest.approx(0.3)
        # Depth = 0.5*0.2 + 0.5*0.3 = 0.25
        assert depth == pytest.approx(0.25)
        # Acceleration = 0.25 / 0.5 = 0.5
        assert a == pytest.approx(0.5)

    def test_saturation_clamps_to_one(self) -> None:
        from services.narrative.attention import _normalize_for_quality

        p, d, depth, a = _normalize_for_quality(
            dwd_14d=5.0,
            unique_authors_14d=500,
            gini_14d=0.0,
            financial_term_density=2.0,
            dd_post_ratio=2.0,
            acceleration_7d=10.0,
        )
        assert p == 1.0
        assert d == 1.0
        assert depth == 1.0
        assert a == 1.0

    def test_negative_accel_floors_at_zero(self) -> None:
        from services.narrative.attention import _normalize_for_quality

        _, _, _, a = _normalize_for_quality(
            dwd_14d=0.5,
            unique_authors_14d=5,
            gini_14d=0.5,
            financial_term_density=0.1,
            dd_post_ratio=0.1,
            acceleration_7d=-0.3,
        )
        assert a == 0.0

    def test_high_gini_kills_diversity(self) -> None:
        from services.narrative.attention import _normalize_for_quality

        _, d, _, _ = _normalize_for_quality(
            dwd_14d=0.5,
            unique_authors_14d=20,
            gini_14d=1.0,
            financial_term_density=0.0,
            dd_post_ratio=0.0,
            acceleration_7d=0.0,
        )
        assert d == 0.0


# ---------------------------------------------------------------------------
# build_snapshot (integration of pure functions)
# ---------------------------------------------------------------------------

class TestBuildSnapshot:
    def _make_signals(
        self,
        anchor: date,
        n: int = 10,
        tickers: str = "NVDA",
        sentiment: str = "bullish",
        confidence: float = 0.85,
    ) -> list[dict]:
        """Generate n synthetic signals spread over the last n days."""
        from datetime import datetime, timezone

        signals = []
        for i in range(n):
            day = anchor - timedelta(days=i)
            ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            signals.append({
                "ticker": tickers,
                "sentiment": sentiment,
                "confidence": confidence,
                "rationale": "Strong earnings beat, raised guidance",
                "author_hash": f"author_{i % 5}",  # 5 distinct authors
                "created_utc": ts,
                "flair": "DD" if i % 3 == 0 else None,
            })
        return signals

    def test_returns_snapshot_with_correct_ticker(self) -> None:
        anchor = date(2026, 5, 1)
        signals = self._make_signals(anchor)
        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.ticker == "NVDA"
        assert snap.bucket_date == "2026-05-01"
        assert snap.id == "NVDA_2026-05-01"
        # §2.5 attention_quality is populated and within [0, 1].
        assert 0.0 <= snap.attention_quality <= 1.0

    def test_mentions_counts_correct(self) -> None:
        anchor = date(2026, 5, 1)
        signals = self._make_signals(anchor, n=14)
        snap = build_snapshot("NVDA", signals, anchor)
        # All 14 signals are within the 14-day window.
        # The 7d window is [anchor-7, anchor] inclusive (8 calendar days, t=0..7).
        assert snap.mentions_14d == 14
        assert snap.mentions_7d == 8

    def test_window_boundary_15d_to_30d(self) -> None:
        """Signals on days 15–30 appear in mentions_30d but NOT mentions_14d/7d."""
        from datetime import datetime, timezone

        anchor = date(2026, 5, 1)
        # One signal exactly 15 days ago (outside 14d window, inside 30d).
        day_15 = anchor - timedelta(days=15)
        ts_15 = int(datetime(day_15.year, day_15.month, day_15.day, tzinfo=timezone.utc).timestamp())
        # One signal exactly 7 days ago (inside 7d window).
        day_7 = anchor - timedelta(days=7)
        ts_7 = int(datetime(day_7.year, day_7.month, day_7.day, tzinfo=timezone.utc).timestamp())
        signals = [
            {"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
             "rationale": "", "author_hash": "a1", "created_utc": ts_15, "flair": None},
            {"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
             "rationale": "", "author_hash": "a2", "created_utc": ts_7, "flair": None},
        ]
        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.mentions_30d == 2    # both signals in 30d window
        assert snap.mentions_14d == 1    # only the 7-day-old signal
        assert snap.mentions_7d == 1     # same

    def test_gini_14d_ignores_pre_window_activity(self) -> None:
        """§2.3 Gini must use 14d-only mention counts, not 30d totals.

        Regression: previously gini_14d used author_total_mentions (30d)
        restricted to the 14d-active author set, which let pre-window
        activity skew the diversity metric.
        """
        from datetime import datetime, timezone

        anchor = date(2026, 5, 1)

        def ts(days_ago: int) -> int:
            d = anchor - timedelta(days=days_ago)
            return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())

        # Two authors, both active inside the 14d window with 1 mention each.
        # Author A *also* posted 10 times 20 days ago (pre-14d-window).
        # 14d-scoped Gini should be ~0 (perfectly equal: 1 vs 1).
        signals = []
        for _ in range(10):
            signals.append({"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
                             "rationale": "", "author_hash": "A", "created_utc": ts(20), "flair": None})
        signals.append({"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
                         "rationale": "", "author_hash": "A", "created_utc": ts(5), "flair": None})
        signals.append({"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
                         "rationale": "", "author_hash": "B", "created_utc": ts(3), "flair": None})

        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.unique_authors_14d == 2
        # If gini_14d used 30d totals it would be ~0.41 (counts [1, 11]).
        # Scoped to 14d it should be exactly 0 (counts [1, 1]).
        assert snap.gini_14d == 0.0

    def test_acceleration_7d_positive_when_recent_spike(self) -> None:
        """acceleration_7d > 0 when last 7 days have signals but older days do not."""
        from datetime import datetime, timezone

        anchor = date(2026, 5, 1)
        # 1 signal/day for days 0–7 (inside 7d window), none for days 8–30.
        # dwd_7d: all window days covered → high (near 1.0).
        # dwd_30d: only recent 8 days covered out of 31 → lower.
        # ⇒ acceleration = (dwd_7d - dwd_30d) / dwd_30d > 0.
        signals = []
        for days_ago in range(0, 8):
            day = anchor - timedelta(days=days_ago)
            ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            signals.append({"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
                             "rationale": "", "author_hash": f"a{days_ago}", "created_utc": ts, "flair": None})
        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.acceleration_7d > 0.0

    def test_acceleration_7d_negative_when_fading(self) -> None:
        """acceleration_7d < 0 when last 7 days have no signals but older days do."""
        from datetime import datetime, timezone

        anchor = date(2026, 5, 1)
        # 1 signal/day for days 8–30 only. No signals in the 7d window.
        # dwd_7d = 0.0; dwd_30d > 0 ⇒ acceleration = -1.0.
        signals = []
        for days_ago in range(8, 31):
            day = anchor - timedelta(days=days_ago)
            ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            signals.append({"ticker": "NVDA", "sentiment": "bullish", "confidence": 0.8,
                             "rationale": "", "author_hash": f"a{days_ago}", "created_utc": ts, "flair": None})
        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.acceleration_7d < 0.0

    def test_all_bullish_ratio_one(self) -> None:
        anchor = date(2026, 5, 1)
        signals = self._make_signals(anchor, sentiment="bullish")
        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.bullish_ratio == pytest.approx(1.0)
        assert snap.bearish_ratio == pytest.approx(0.0)

    def test_unique_authors_correct(self) -> None:
        anchor = date(2026, 5, 1)
        signals = self._make_signals(anchor, n=10)
        # 5 distinct authors cycling (author_0..author_4)
        snap = build_snapshot("NVDA", signals, anchor)
        assert snap.unique_authors_14d == 5

    def test_decay_density_between_zero_and_one(self) -> None:
        anchor = date(2026, 5, 1)
        signals = self._make_signals(anchor, n=20)
        snap = build_snapshot("NVDA", signals, anchor)
        assert 0.0 < snap.decay_weighted_density_14d <= 1.0
        assert 0.0 < snap.decay_weighted_density_7d <= 1.0

    def test_empty_signals_returns_zeros(self) -> None:
        snap = build_snapshot("TSLA", [], date(2026, 5, 1))
        assert snap.mentions_7d == 0
        assert snap.mentions_14d == 0
        assert snap.mentions_30d == 0
        assert snap.decay_weighted_density_14d == 0.0
        assert snap.bullish_ratio == 0.0
        assert snap.gini_14d == 0.0

    def test_daily_buckets_sorted_ascending(self) -> None:
        anchor = date(2026, 5, 5)
        signals = self._make_signals(anchor, n=5)
        snap = build_snapshot("NVDA", signals, anchor)
        days = [b.day for b in snap.daily_buckets]
        assert days == sorted(days)

    def test_dd_ratio_nonzero_when_signals_have_dd_flair(self) -> None:
        anchor = date(2026, 5, 1)
        signals = self._make_signals(anchor, n=9)  # every 3rd has flair="DD"
        snap = build_snapshot("NVDA", signals, anchor)
        # 3 out of 9 signals within 14d window should have DD flair.
        # (signals within 14d = first 9, all within window)
        assert snap.dd_post_ratio > 0.0
