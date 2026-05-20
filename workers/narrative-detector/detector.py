"""HDBSCAN clustering + lifecycle stage assignment (Phase 5).

Implements §4 of NARRATIVE_METHODOLOGY.md:

  cluster()     — HDBSCAN on cosine distance, then merges nearby cluster centroids,
                  then applies an intra-cluster similarity floor (ADR-0026).
  assign_stage() — pure signal-side lifecycle rules (no LLM).

Design decisions per ADR-0017 / ADR-0026:
  - min_cluster_size=3 (configurable), metric="cosine" (via precomputed distance matrix).
  - Clusters with cosine similarity > merge_threshold (default 0.82) between centroids
    are merged into a single narrative thread.
  - Clusters whose mean pairwise cosine similarity falls below
    min_intra_cluster_similarity (default 0.35) are demoted to noise — they represent
    semantically unrelated posts that happened to be nearest neighbours rather than a
    shared narrative thread.
  - Noise points (label -1) are excluded from lifecycle assignment.
  - Stage assignment is deterministic; confidence = fraction of signals in the
    dominant cluster relative to total non-noise signals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class ClusterResult:
    """Output of cluster() for a single ticker."""
    labels: list[int]          # per-signal cluster label (-1 = noise)
    n_clusters: int            # number of clusters after merging
    dominant_cluster: int      # label of the largest non-noise cluster (-1 if all noise)
    dominant_fraction: float   # fraction of non-noise signals in the dominant cluster
    n_embedded: int = 0        # total signals fed to HDBSCAN (== len(labels))


def cluster(
    embeddings: list[list[float]],
    min_cluster_size: int = 3,
    merge_threshold: float = 0.82,
    min_intra_cluster_similarity: float = 0.35,
) -> ClusterResult:
    """Run HDBSCAN on embeddings (cosine metric) and merge nearby cluster centroids.

    After merging, any cluster whose mean pairwise cosine similarity is below
    *min_intra_cluster_similarity* is demoted to noise (label -1).  This prevents
    low-coherence pairs — posts that happen to be nearest neighbours but discuss
    unrelated topics — from being promoted to a narrative stage.

    Args:
        embeddings: list of 1536-dim float vectors.
        min_cluster_size: HDBSCAN parameter (ADR-0017 default=3).
        merge_threshold: cosine similarity above which two clusters are merged.
        min_intra_cluster_similarity: quality floor — clusters below this mean
            pairwise similarity are treated as noise (ADR-0026 default=0.35).

    Returns:
        ClusterResult with per-signal labels and summary stats.
    """
    n = len(embeddings)
    if n == 0:
        return ClusterResult(
            labels=[], n_clusters=0, dominant_cluster=-1,
            dominant_fraction=0.0, n_embedded=0,
        )

    # HDBSCAN needs >=2 samples to fit, and >= min_cluster_size to form any
    # cluster. Below that threshold everything is noise by definition — return
    # a trivial result rather than letting sklearn raise. This is common in the
    # ramp-up window where a ticker may have a single embedded signal.
    if n < max(2, min_cluster_size):
        return ClusterResult(
            labels=[-1] * n,
            n_clusters=0,
            dominant_cluster=-1,
            dominant_fraction=0.0,
            n_embedded=n,
        )

    mat = np.array(embeddings, dtype=np.float32)

    # Normalise rows so that cosine distance = 1 - dot product.
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    mat_normed = mat / norms

    # Precomputed cosine distance matrix: D[i,j] = 1 - cos(i,j), range [0,2].
    cos_sim = cosine_similarity(mat_normed)
    dist_matrix = np.clip(1.0 - cos_sim, 0.0, 2.0).astype(np.float64)

    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        metric="precomputed",
    )
    raw_labels: np.ndarray = clusterer.fit_predict(dist_matrix)

    # --- Merge clusters whose centroids are cosine-similar > merge_threshold ---
    unique_labels = [lbl for lbl in set(raw_labels.tolist()) if lbl != -1]
    if len(unique_labels) > 1:
        centroids = np.array(
            [mat_normed[raw_labels == lbl].mean(axis=0) for lbl in unique_labels]
        )
        centroid_sim = cosine_similarity(centroids)
        # Union-find: merge label pairs above threshold.
        parent = {lbl: lbl for lbl in unique_labels}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, lbl_i in enumerate(unique_labels):
            for j, lbl_j in enumerate(unique_labels):
                if j <= i:
                    continue
                if centroid_sim[i, j] >= merge_threshold:
                    ri, rj = find(lbl_i), find(lbl_j)
                    if ri != rj:
                        parent[rj] = ri

        # Remap all labels through the union-find roots.
        remap = {lbl: find(lbl) for lbl in unique_labels}
        merged_labels = np.array(
            [remap[lbl] if lbl != -1 else -1 for lbl in raw_labels.tolist()],
            dtype=np.int64,
        )
        # Re-number contiguously (0, 1, 2 ...) preserving -1.
        roots = sorted(set(remap.values()))
        root_to_idx = {r: idx for idx, r in enumerate(roots)}
        final_labels = np.array(
            [root_to_idx[lbl] if lbl != -1 else -1 for lbl in merged_labels.tolist()],
            dtype=np.int64,
        )
    else:
        final_labels = raw_labels.copy()

    # --- Intra-cluster similarity floor (ADR-0026) ---
    # For each surviving cluster, compute the mean pairwise cosine similarity of
    # its members. A pair of posts that HDBSCAN grouped purely because they were
    # the two closest points in a sparse space will have a low mean similarity;
    # a genuine shared-narrative cluster will score well above the floor.
    if min_intra_cluster_similarity > 0.0:
        for lbl in set(final_labels.tolist()):
            if lbl == -1:
                continue
            idxs = np.where(final_labels == lbl)[0]
            if len(idxs) < 2:
                # Degenerate singleton — demote (should not occur with min_cluster_size>=2).
                final_labels[final_labels == lbl] = -1
                continue
            sub = cos_sim[np.ix_(idxs, idxs)]
            n_pairs = len(idxs) * (len(idxs) - 1)
            mean_sim = float((sub.sum() - np.trace(sub)) / n_pairs)
            if mean_sim < min_intra_cluster_similarity:
                final_labels[final_labels == lbl] = -1
                logger.debug(
                    "cluster(): label=%d demoted to noise "
                    "(mean_intra_sim=%.3f < floor=%.3f, n=%d)",
                    lbl, mean_sim, min_intra_cluster_similarity, len(idxs),
                )

    labels_list = final_labels.tolist()
    non_noise = [lbl for lbl in labels_list if lbl != -1]
    n_clusters = len(set(non_noise))

    if non_noise:
        from collections import Counter
        counts = Counter(non_noise)
        dominant_cluster, dominant_count = counts.most_common(1)[0]
        dominant_fraction = dominant_count / len(non_noise)
    else:
        dominant_cluster = -1
        dominant_fraction = 0.0

    logger.debug(
        "cluster(): n=%d raw_clusters=%d merged_clusters=%d dominant_fraction=%.2f",
        n, len(set(raw_labels.tolist()) - {-1}), n_clusters, dominant_fraction,
    )
    return ClusterResult(
        labels=labels_list,
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

    # Step 6 — Confidence.  Coherence floored so polysemic clusters still
    # produce a usable signal; volume factor dampens thin clusters.
    coherence = max(cluster_result.dominant_fraction, COHERENCE_FLOOR)
    volume_factor = min(cluster_result.n_embedded / N_VOLUME_FULL, 1.0)
    base_confidence = compute_confidence(
        score=score,
        target_stage=target_stage,
        committed_stage=committed,
        dominant_fraction=coherence,
    )
    confidence = round(base_confidence * volume_factor, 4)
    return committed, confidence, new_state
