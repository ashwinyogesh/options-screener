"""Pure attention-metric computation functions (§2 of NARRATIVE_METHODOLOGY.md).

All functions are side-effect-free and take only plain Python values so they
can be unit-tested without Cosmos, yfinance, or any I/O.

Usage (Phase 3 aggregator):
    from services.narrative.attention import build_snapshot
    snapshot = build_snapshot(ticker, signals, bucket_date)

§2 dimensions implemented here:
    2.1 Persistence   — decay_weighted_density() with λ=0.1
    2.2 Acceleration  — compute_acceleration()
    2.3 Diversity     — compute_gini()
    2.4 Depth         — compute_financial_term_density(), dd_post_ratio

§5 ACS component pre-computation:
    Component A input  — decay_weighted_density_14d (normalized [0,1])
    Component B input  — unique_authors_14d, mentions_14d, gini_14d
    (Components C–E require Phase 4/5/6 data; not computed here.)
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from types_ import DailyBucket, TickerTimelineSnapshot

# ---------------------------------------------------------------------------
# Constants — match §2 and SCORING_REFERENCE.md. Change only via ADR.
# ---------------------------------------------------------------------------
_DECAY_LAMBDA: float = 0.1          # §2.1 — half-life ≈ 6.9 days
_WINDOW_7D: int = 7
_WINDOW_14D: int = 14
_WINDOW_30D: int = 30

# Terms that indicate a financially substantive post (§2.4).
# Deliberately conservative — false positives (calling a shallow post deep)
# are worse than false negatives.
_FINANCIAL_TERMS: frozenset[str] = frozenset({
    "revenue", "earnings", "eps", "ebitda", "pe", "p/e", "margin", "guidance",
    "catalyst", "valuation", "dcf", "bull", "bear", "thesis", "short", "long",
    "calls", "puts", "iv", "oi", "open interest", "float", "shares", "dilution",
    "buyback", "dividend", "debt", "fcf", "free cash", "moat", "competitive",
    "market share", "gross margin", "operating", "capex", "sector", "index",
    "analyst", "upgrade", "downgrade", "target price", "pt ", "beat", "miss",
    "guidance", "outlook", "macro", "fed", "rate", "inflation", "yield",
})

# Flair or title keywords that mark a DD (due diligence) post (§2.4).
_DD_TERMS: frozenset[str] = frozenset({
    "dd", "due diligence", "deep dive", "analysis", "thesis", "research",
    "writeup", "write-up", "bull case", "bear case",
})


# ---------------------------------------------------------------------------
# §2.1 — Persistence: decay-weighted density
# ---------------------------------------------------------------------------

def decay_weighted_density(
    daily_counts: Sequence[tuple[date, int]],
    reference_date: date,
    window_days: int,
    lam: float = _DECAY_LAMBDA,
) -> float:
    """Return the exponentially decay-weighted mention density for a window.

    w(t) = e^(-λ·t) where t = days before reference_date (0 = today).
    Result is normalized to [0, 1] by dividing by the sum of weights so that
    the value is comparable across windows of different lengths.

    Args:
        daily_counts: (day, mention_count) pairs. Days outside the window are
                      ignored. Missing days are treated as count=0.
        reference_date: the anchor date (typically today UTC).
        window_days: rolling window size in days (7, 14, or 30).
        lam: decay constant λ. Default 0.1 per §2.1.

    Returns:
        float in [0, 1]. 0.0 if no mentions in window.
    """
    cutoff = reference_date - timedelta(days=window_days)
    weight_sum = 0.0
    weighted_count = 0.0

    for day, count in daily_counts:
        if day < cutoff or day > reference_date:
            continue
        t = (reference_date - day).days  # 0 = today, window_days-1 = oldest
        w = math.exp(-lam * t)
        weighted_count += w * count
        weight_sum += w

    if weight_sum == 0.0:
        return 0.0
    # Normalize by the theoretical max weight: the sum of e^(-λt) over every day
    # in the window (t=0 .. window_days), assuming count=1 per day.  This makes
    # the result a true occupancy fraction — 1.0 means every day in the window had
    # at least one signal right at the reference date, 0.0 means none.
    # Dividing by `weight_sum` (the old approach) only accumulated weights for
    # days that *had* signals, making the function return 1.0 for any non-empty
    # input — defeating the purpose of persistence measurement.
    max_weight = sum(math.exp(-lam * t) for t in range(window_days + 1))
    return min(weighted_count / max_weight, 1.0)


# ---------------------------------------------------------------------------
# §2.2 — Acceleration
# ---------------------------------------------------------------------------

def compute_acceleration(
    density_7d: float,
    density_30d: float,
) -> float:
    """Return ΔV/Δt: (7d density - 30d baseline) / 30d baseline.

    Positive = accelerating (recent activity above baseline).
    Negative = decelerating.
    Zero-safe: returns 0.0 if 30d baseline is zero.
    Result is unbounded; caller clips for display purposes.
    """
    if density_30d == 0.0:
        return 0.0
    return (density_7d - density_30d) / density_30d


# ---------------------------------------------------------------------------
# §2.3 — Contributor diversity: Gini coefficient
# ---------------------------------------------------------------------------

def compute_gini(mention_counts: Sequence[int]) -> float:
    """Return the Gini coefficient of the contribution distribution.

    G = 0 → perfectly equal (every author mentioned the ticker the same number
              of times). Healthy, organic.
    G = 1 → perfectly concentrated (one author made all mentions). Flag.

    Thresholds per §2.3: G < 0.35 healthy, G > 0.65 concentration flag.

    Args:
        mention_counts: list of per-author mention counts (any order).

    Returns:
        float in [0, 1]. Returns 0.0 for empty or single-author lists.
    """
    counts = [c for c in mention_counts if c > 0]
    n = len(counts)
    if n <= 1:
        return 0.0
    counts_sorted = sorted(counts)
    total = sum(counts_sorted)
    if total == 0:
        return 0.0
    gini_sum = 0.0
    for i, c in enumerate(counts_sorted, start=1):
        gini_sum += (2 * i - n - 1) * c
    return gini_sum / (n * total)


# ---------------------------------------------------------------------------
# §2.4 — Discussion depth
# ---------------------------------------------------------------------------

def compute_financial_term_density(bodies: Sequence[str]) -> float:
    """Return the average fraction of tokens that are financial terms.

    A token matches if it's a substring of a body (case-insensitive).
    Quality signal: density > 0.12 per §2.4.

    Returns float in [0, 1]. 0.0 for empty input.
    """
    if not bodies:
        return 0.0
    densities = []
    for body in bodies:
        if not body:
            densities.append(0.0)
            continue
        lower = body.lower()
        tokens = lower.split()
        if not tokens:
            densities.append(0.0)
            continue
        hits = sum(1 for term in _FINANCIAL_TERMS if term in lower)
        # Cap at 1 hit per term per post; normalize by token count
        densities.append(min(hits / len(tokens), 1.0))
    return sum(densities) / len(densities)


def compute_dd_post_ratio(flairs: Sequence[str | None], bodies: Sequence[str]) -> float:
    """Return the fraction of posts that appear to be DD/analysis posts.

    Checks flair text and first 200 chars of body for DD keywords (§2.4).
    Returns float in [0, 1]. 0.0 for empty input.
    """
    if not bodies:
        return 0.0
    total = len(bodies)
    dd_count = 0
    for flair, body in zip(flairs, bodies):
        flair_lower = (flair or "").lower()
        body_lower = (body or "")[:200].lower()
        if any(t in flair_lower or t in body_lower for t in _DD_TERMS):
            dd_count += 1
    return dd_count / total


# ---------------------------------------------------------------------------
# §2.5 — Composite attention quality score
# ---------------------------------------------------------------------------

def compute_attention_quality(
    persistence: float,
    contributor_diversity: float,
    discussion_depth: float,
    acceleration: float,
) -> float:
    """Return §2.5 composite attention quality in [0, 1].

    Weights per §2.5:
        persistence           0.35
        contributor_diversity 0.25
        discussion_depth      0.25
        acceleration          0.15

    All inputs should be normalized to [0, 1] before calling.
    Acceleration is clipped to [0, 1] here (negative acceleration = 0 contrib).
    """
    accel_clipped = max(0.0, min(acceleration, 1.0))
    return (
        0.35 * persistence
        + 0.25 * contributor_diversity
        + 0.25 * discussion_depth
        + 0.15 * accel_clipped
    )


# ---------------------------------------------------------------------------
# Main builder — called by the Phase 3 aggregator
# ---------------------------------------------------------------------------

def build_snapshot(
    ticker: str,
    signals: list[dict],
    bucket_date: date,
) -> TickerTimelineSnapshot:
    """Compute a TickerTimelineSnapshot from raw Cosmos signals documents.

    Args:
        ticker: uppercase ticker symbol, e.g. "NVDA".
        signals: list of Cosmos signal documents for this ticker over the last
                 30 days. Each dict must have at minimum:
                   created_utc (int), sentiment (str), confidence (float),
                   author_hash (str), body (str via post body from ingestion),
                   flair (str | None).
        bucket_date: the UTC date this snapshot is anchored to (today).

    Returns:
        TickerTimelineSnapshot ready to upsert into Cosmos ticker_timeline.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    bucket_str = bucket_date.isoformat()

    # --- Bucket signals by UTC date ---
    counts_by_day: dict[date, int] = defaultdict(int)
    authors_by_day: dict[date, set[str]] = defaultdict(set)
    author_total_mentions: dict[str, int] = defaultdict(int)

    bodies_14d: list[str] = []
    flairs_14d: list[str | None] = []
    sentiments_14d: list[str] = []
    confidences_14d: list[float] = []

    cutoff_30d = bucket_date - timedelta(days=_WINDOW_30D)
    cutoff_14d = bucket_date - timedelta(days=_WINDOW_14D)

    for sig in signals:
        ts = sig.get("created_utc", 0)
        sig_date = datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else bucket_date
        if sig_date < cutoff_30d or sig_date > bucket_date:
            continue

        counts_by_day[sig_date] += 1
        author = sig.get("author_hash", "")
        if author:
            authors_by_day[sig_date].add(author)
            author_total_mentions[author] += 1

        if sig_date >= cutoff_14d:
            bodies_14d.append(sig.get("rationale", "") or "")
            flairs_14d.append(sig.get("flair"))
            sentiments_14d.append(sig.get("sentiment", "neutral"))
            confidences_14d.append(float(sig.get("confidence", 0.0)))

    # --- Build daily_buckets for 30d window ---
    daily_buckets = [
        DailyBucket(
            day=d.isoformat(),
            count=counts_by_day[d],
            unique_authors=len(authors_by_day[d]),
        )
        for d in sorted(counts_by_day)
    ]
    daily_pairs = [(d, counts_by_day[d]) for d in counts_by_day]

    # --- Window counts ---
    cutoff_7d = bucket_date - timedelta(days=_WINDOW_7D)
    mentions_7d = sum(c for d, c in daily_pairs if d >= cutoff_7d)
    mentions_14d = sum(c for d, c in daily_pairs if d >= cutoff_14d)
    mentions_30d = sum(c for d, c in daily_pairs)

    # --- §2.1 Persistence ---
    dwd_7d = decay_weighted_density(daily_pairs, bucket_date, _WINDOW_7D)
    dwd_14d = decay_weighted_density(daily_pairs, bucket_date, _WINDOW_14D)
    dwd_30d = decay_weighted_density(daily_pairs, bucket_date, _WINDOW_30D)

    # --- §2.2 Acceleration ---
    accel = compute_acceleration(dwd_7d, dwd_30d)

    # --- §2.3 Diversity ---
    all_14d_authors: set[str] = set()
    for d, authors in authors_by_day.items():
        if d >= cutoff_14d:
            all_14d_authors.update(authors)
    unique_authors_14d = len(all_14d_authors)

    mention_counts_14d = [
        author_total_mentions[a]
        for a in all_14d_authors
    ]
    gini_14d = compute_gini(mention_counts_14d)

    # --- §2.4 Depth ---
    ft_density = compute_financial_term_density(bodies_14d)
    dd_ratio = compute_dd_post_ratio(flairs_14d, bodies_14d)

    # --- Sentiment ratios (Phase 2 extractor output, not conviction states) ---
    total_s = len(sentiments_14d)
    bullish_ratio = sentiments_14d.count("bullish") / total_s if total_s else 0.0
    bearish_ratio = sentiments_14d.count("bearish") / total_s if total_s else 0.0
    avg_confidence = sum(confidences_14d) / total_s if total_s else 0.0

    avg_body_len = (
        sum(len(b) for b in bodies_14d) / len(bodies_14d) if bodies_14d else 0.0
    )

    return TickerTimelineSnapshot(
        id=f"{ticker}_{bucket_str}",
        ticker=ticker,
        bucket_date=bucket_str,
        computed_at=now_iso,
        mentions_7d=mentions_7d,
        mentions_14d=mentions_14d,
        mentions_30d=mentions_30d,
        decay_weighted_density_7d=dwd_7d,
        decay_weighted_density_14d=dwd_14d,
        decay_weighted_density_30d=dwd_30d,
        daily_buckets=daily_buckets,
        acceleration_7d=accel,
        unique_authors_14d=unique_authors_14d,
        gini_14d=gini_14d,
        avg_body_len=avg_body_len,
        dd_post_ratio=dd_ratio,
        financial_term_density=ft_density,
        bullish_ratio=bullish_ratio,
        bearish_ratio=bearish_ratio,
        avg_confidence=avg_confidence,
    )
