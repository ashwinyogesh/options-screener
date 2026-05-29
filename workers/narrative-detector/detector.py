"""Cosine-graph clustering + lifecycle stage assignment (Phase 5).

Implements §4 of NARRATIVE_METHODOLOGY.md.

Clustering approach (replaces HDBSCAN, see ADR-0035):
  cluster()     — pairwise cosine similarity graph: two signals are in the
                  same cluster iff cosine(a, b) >= CLUSTER_SIMILARITY_FLOOR.
                  Connected components of the resulting graph define clusters.
                  This is deterministic, requires no hyperparameters, and
                  degrades gracefully at low signal volumes (2–18 signals/72h
                  is the typical range per ADR-0026 observations).

  assign_stage() — pure signal-side lifecycle rules (no LLM).

Why the change from HDBSCAN:
  HDBSCAN with min_cluster_size=3 needs ≥3 samples to form any cluster;
  at typical ingestion volumes most tickers had all signals labelled as
  noise (-1) and stage assignment was arbitrary.  The cosine-graph approach
  groups any two semantically similar signals together, making it well-suited
  to sparse signal environments.  When signal volumes grow past ~30/72h,
  the approach naturally produces multiple disconnected components.

  See ADR-0035 for the full decision record.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Cosine similarity floor for the cluster graph edge.
# Two signals are considered to share a narrative thread if their embedding
# cosine similarity meets this threshold.  Calibrated at 0.45:
#   < 0.35 — semantically unrelated (ADR-0026 observed noise floor)
#   0.35–0.45 — marginal overlap
#   > 0.45 — clearly shared topic
# Raise this to tighten clustering (fewer, more coherent clusters);
# lower to loosen (more inclusive but noisier).
CLUSTER_SIMILARITY_FLOOR: float = 0.45


@dataclass
class ClusterResult:
    """Output of cluster() for a single ticker."""
    labels: list[int]          # per-signal cluster label (-1 = noise / singleton)
    n_clusters: int            # number of clusters (≥2 members each)
    dominant_cluster: int      # label of the largest cluster (-1 if none)
    dominant_fraction: float   # fraction of clustered signals in the dominant cluster
    n_embedded: int = 0        # total signals fed to the algorithm


def cluster(
    embeddings: list[list[float]],
    similarity_floor: float = CLUSTER_SIMILARITY_FLOOR,
) -> ClusterResult:
    """Cluster embeddings using a pairwise cosine similarity graph.

    Builds a graph where nodes are signals and edges connect pairs whose
    cosine similarity >= similarity_floor.  Connected components of this
    graph are the clusters.  Singleton nodes (no edge to any other signal)
    are labelled -1 (noise) — they represent posts that don't share enough
    semantic content with any other post to form a narrative thread.

    Algorithm:
        1. Normalise embeddings to unit vectors.
        2. Compute all-pairs cosine similarity matrix.
        3. Threshold to adjacency matrix (sim >= floor → 1, else 0).
        4. BFS/union-find to find connected components.
        5. Singleton components → label -1.
        6. Number remaining components 0, 1, 2 … by descending size.

    Properties:
        - Deterministic (no random state).
        - No minimum cluster size hyperparameter — any pair of similar
          signals forms a cluster.
        - O(n²) time and space — acceptable up to ~500 signals per ticker.
          At 18 signals/72h this is trivially fast.

    Args:
        embeddings: list of float vectors (any dimension).
        similarity_floor: minimum cosine similarity to draw an edge.

    Returns:
        ClusterResult with per-signal labels and summary statistics.
    """
    n = len(embeddings)
    if n == 0:
        return ClusterResult(labels=[], n_clusters=0, dominant_cluster=-1,
                             dominant_fraction=0.0, n_embedded=0)
    if n == 1:
        return ClusterResult(labels=[-1], n_clusters=0, dominant_cluster=-1,
                             dominant_fraction=0.0, n_embedded=1)

    mat = np.array(embeddings, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    mat_normed = mat / norms

    sim = cosine_similarity(mat_normed)   # (n, n) in [-1, 1]

    # Adjacency: 1 if sim >= floor AND i != j.
    adj = (sim >= similarity_floor).astype(bool)
    np.fill_diagonal(adj, False)

    # Union-find connected components.
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if adj[i, j]:
                union(i, j)

    # Map each node to its root component.
    roots = [find(i) for i in range(n)]

    # Group by root; singletons (root == self and no edges) → noise.
    from collections import defaultdict
    component: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(roots):
        component[r].append(i)

    # Any component with only 1 member is a singleton → label -1.
    labels = [-1] * n
    cluster_idx = 0
    cluster_sizes: list[tuple[int, int]] = []  # (cluster_idx, size)
    for members in component.values():
        if len(members) < 2:
            continue  # singleton stays -1
        for m in members:
            labels[m] = cluster_idx
        cluster_sizes.append((cluster_idx, len(members)))
        cluster_idx += 1

    n_clusters = cluster_idx
    non_noise = [l for l in labels if l != -1]

    if non_noise:
        counts = Counter(non_noise)
        dominant_cluster, dominant_count = counts.most_common(1)[0]
        dominant_fraction = dominant_count / len(non_noise)
    else:
        dominant_cluster = -1
        dominant_fraction = 0.0

    logger.debug(
        "cluster(): n=%d n_clusters=%d dominant_fraction=%.2f floor=%.2f",
        n, n_clusters, dominant_fraction, similarity_floor,
    )
    return ClusterResult(
        labels=labels,
        n_clusters=n_clusters,
        dominant_cluster=dominant_cluster,
        dominant_fraction=dominant_fraction,
        n_embedded=n,
    )


# ---------------------------------------------------------------------------
# Lifecycle stage assignment — smoothed inputs + monotone hysteresis (ADR-0030).
# ---------------------------------------------------------------------------

from smoothing import (  # noqa: E402
    LifecycleState,
    apply_hysteresis,
    breadth_score,
    breadth_to_stage,
    compute_confidence,
    ema_smooth,
    overlay_stage,
)

# Minimum embedded signals required to attempt classification.  Below this we
# return stage 0 ("insufficient data") — there genuinely isn't enough volume
# to assert a narrative.  Set to match HDBSCAN's min_cluster_size floor.
N_MIN_EMBEDDED: int = 5

# Volume of embedded signals at which confidence reaches its full value.  Below
# this the volume_factor scales linearly down toward zero so thin clusters
# don't drive high-confidence callouts.  Above this we saturate.
N_VOLUME_FULL: int = 10

# Floor applied to dominant_fraction inside the confidence calculation so that
# polysemic narratives (multiple low-coherence sub-themes — common for
# megacaps like GOOGL where posts span cloud / AI / antitrust / Waymo) still
# receive a non-zero confidence rather than silently disappearing.
#
# The floor is applied as a *linear remap* (not a hard clamp):
#     coherence = COHERENCE_FLOOR + (1 - COHERENCE_FLOOR) * dominant_fraction
# so dominant_fraction in [0, 1] maps continuously to [floor, 1.0]. The hard
# clamp version collapsed every polysemic ticker onto the exact same value,
# which (combined with a saturated volume_factor) produced the observed
# pile-up of tickers at C = 20.0 in the UI.
COHERENCE_FLOOR: float = 0.3


def assign_stage(
    timeline: dict,
    cluster_result: ClusterResult,
    prior_state: LifecycleState | None = None,
    prev_stage: int = 0,
) -> tuple[int, float, LifecycleState]:
    """Return (lifecycle_stage, stage_confidence, new_state) for a ticker.

    Implements ADR-0030 stability rules and the GOOGL fix amendment:

      1. EMA-smooth volatile aggregator inputs (alpha=0.4, ~3-run half-life).
      2. **Gate** on ``n_embedded`` (count of usable signals), NOT on
         ``n_clusters``.  Below ``N_MIN_EMBEDDED`` → stage 0.  At or above,
         we always assign a stage even if HDBSCAN found no coherent cluster
         (a polysemic ticker is still in *some* lifecycle stage based on
         tier1/growth/dd_post).
      3. Compute a continuous breadth score from smoothed inputs; map to a
         breadth stage in {1, 2, 3}.
      4. Override with Stage 5/6 when axis overlay condition holds against
         smoothed axis shares.
      5. Apply monotone hysteresis: cap movement to ±1 stage per commit,
         require 2 consecutive runs at the new target.
      6. Confidence absorbs cluster coherence (``dominant_fraction``, floored
         at ``COHERENCE_FLOOR``) and volume (``min(n_embedded/N_VOLUME_FULL, 1)``)
         as multiplicative factors — a polysemic 18-post cluster lands the
         same stage but at lower confidence than a coherent 18-post cluster.

    Args:
        timeline: today's ticker_timeline Cosmos document.
        cluster_result: output of cluster() for the ticker's 72h window.
        prior_state: hysteresis + smoothing state from the previous detector
            run (today earlier, or yesterday).  None → cold start.
        prev_stage: previously committed lifecycle_stage.  0 → cold start.

    Returns:
        (committed_stage, confidence, new_state).  ``committed_stage == 0``
        means insufficient data (n_embedded below floor); ``new_state``
        carries forward unchanged in that case.
    """
    if prior_state is None:
        prior_state = LifecycleState()

    # Stage 0 — insufficient data.  Genuine "not enough signal to classify".
    # Polysemic tickers (n_embedded high but n_clusters == 0) fall through.
    if cluster_result.n_embedded < N_MIN_EMBEDDED:
        return 0, 0.0, prior_state

    # Step 1 — EMA smoothing.
    smoothed = ema_smooth(timeline, prior_state.smoothed_inputs)

    # Step 2 — Continuous breadth score.
    score = breadth_score(smoothed)

    # Step 3 — Map to breadth stage 1/2/3.
    target_breadth = breadth_to_stage(score)

    # Step 4 — Axis overlay can replace breadth stage with 5/6.
    target_overlay = overlay_stage(smoothed)
    target_stage = target_overlay if target_overlay is not None else target_breadth

    # Step 5 — Hysteresis.  The overlay (5/6) and breadth band (1/2/3) are
    # treated as a single ordered chain: 1 → 2 → 3 → 5 → 6 (4 reserved).
    interim_state = LifecycleState(
        smoothed_inputs=smoothed,
        pending_stage=prior_state.pending_stage,
        pending_streak=prior_state.pending_streak,
    )
    committed, new_state = apply_hysteresis(target_stage, prev_stage, interim_state)

    # Step 6 — Confidence.
    #
    # Coherence is a *linear remap* of dominant_fraction into [floor, 1.0]
    # (see COHERENCE_FLOOR docstring): a hard floor would collapse every
    # polysemic ticker onto the same value and prevent the C column from
    # discriminating between them.
    #
    # Volume factor uses a continuous saturating curve (1 - exp(-n/N_VOLUME_FULL))
    # instead of the prior linear-with-hard-knee min(n/N_VOLUME_FULL, 1.0).
    # The exponential curve never has a discontinuity at n_embedded = 10,
    # so a cluster that grows from 9 → 11 signals no longer sees its volume
    # weighting jump to a permanent 1.0 plateau; instead it keeps moving
    # smoothly (0.593 → 0.667 → ... → 0.95 around n=30). Combined with the
    # coherence remap above, this removes the quantisation that was clumping
    # the UI's confidence values onto a discrete lattice.
    dom_frac = cluster_result.dominant_fraction
    coherence = COHERENCE_FLOOR + (1.0 - COHERENCE_FLOOR) * dom_frac
    volume_factor = 1.0 - math.exp(-cluster_result.n_embedded / N_VOLUME_FULL)
    base_confidence = compute_confidence(
        score=score,
        target_stage=target_stage,
        committed_stage=committed,
        dominant_fraction=coherence,
    )
    confidence = round(base_confidence * volume_factor, 4)
    return committed, confidence, new_state
