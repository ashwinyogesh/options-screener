"""HDBSCAN clustering + lifecycle stage assignment (Phase 5).

Implements §4 of NARRATIVE_METHODOLOGY.md:

  cluster()     — HDBSCAN on cosine distance, then merges nearby cluster centroids.
  assign_stage() — pure signal-side lifecycle rules (no LLM).

Design decisions per ADR-0017:
  - min_cluster_size=3 (configurable), metric="cosine" (via precomputed distance matrix).
  - Clusters with cosine similarity > merge_threshold (default 0.82) between centroids
    are merged into a single narrative thread.
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


def cluster(
    embeddings: list[list[float]],
    min_cluster_size: int = 3,
    merge_threshold: float = 0.82,
) -> ClusterResult:
    """Run HDBSCAN on embeddings (cosine metric) and merge nearby cluster centroids.

    Args:
        embeddings: list of 1536-dim float vectors.
        min_cluster_size: HDBSCAN parameter (ADR-0017 default=3).
        merge_threshold: cosine similarity above which two clusters are merged.

    Returns:
        ClusterResult with per-signal labels and summary stats.
    """
    n = len(embeddings)
    if n == 0:
        return ClusterResult(labels=[], n_clusters=0, dominant_cluster=-1, dominant_fraction=0.0)

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
    )


# ---------------------------------------------------------------------------
# Lifecycle stage assignment — pure signal-side rules per §4.
# ---------------------------------------------------------------------------

def assign_stage(
    timeline: dict,
    cluster_result: ClusterResult,
) -> tuple[int, float]:
    """Return (lifecycle_stage, stage_confidence) for a ticker.

    Args:
        timeline: ticker_timeline Cosmos document (may be a minimal stub if
                  aggregator hasn't run yet — missing fields default to 0/None).
        cluster_result: output of cluster() for the ticker's 72h window.

    Returns:
        (stage, confidence) where stage ∈ {1..6, 0} and confidence ∈ [0,1].
        Stage 0 means "insufficient data to classify".

    Stage rules from §4 of NARRATIVE_METHODOLOGY.md:
        1 — Niche technical:     tier1_pct < 0.20  AND financial_term_density > 0.15
        2 — Early conviction:    tier1_pct ∈ [0.20,0.50]  AND dd_post_ratio > 0.10 AND gini < 0.45
        3 — Expanding awareness: contributor_count_growth_7d > 0.30  (tier2 rising proxy)
        4 — Institutional attn:  (not computable at Phase 5 — external_media/analyst data absent)
        5 — Consensus:           conviction_emotional_bull_ratio > 0.50 AND gini < 0.30
        6 — Saturation:          conviction_emotional_bull_ratio > 0.65 AND gini_14d > 0.55
    """
    if cluster_result.n_clusters == 0:
        return 0, 0.0

    # Pull fields from timeline doc with safe defaults.
    tier1_pct: float = timeline.get("tier1_pct") or 0.0
    tier2_pct: float = timeline.get("tier2_pct") or 0.0  # noqa: F841 — reserved for Stage 3 ext
    gini_14d: float = timeline.get("gini_14d") or 0.0
    dd_post_ratio: float = timeline.get("dd_post_ratio") or 0.0
    financial_term_density: float = timeline.get("financial_term_density") or 0.0
    # contributor_count_growth_7d: not yet stored by aggregator; default 0.0
    contributor_growth: float = timeline.get("contributor_count_growth_7d") or 0.0
    emotional_bull_ratio: float = timeline.get("conviction_emotional_bull_ratio") or 0.0

    # Confidence base = fraction of non-noise signals in dominant cluster,
    # weighted by how cleanly the rule matches.
    base_conf = cluster_result.dominant_fraction

    # Rules evaluated in reverse priority (later stages override earlier).
    stage = 0
    conf = 0.0

    # Stage 1 — Niche technical
    if tier1_pct < 0.20 and financial_term_density > 0.15:
        stage = 1
        conf = base_conf * 0.7  # lower confidence — early, sparse signal

    # Stage 2 — Early conviction (overrides Stage 1 if broader)
    if 0.20 <= tier1_pct <= 0.50 and dd_post_ratio > 0.10 and gini_14d < 0.45:
        stage = 2
        conf = base_conf

    # Stage 3 — Expanding awareness
    if contributor_growth > 0.30:
        stage = 3
        conf = base_conf

    # Stage 4 — Institutional attention (requires external_media/analyst data — not Phase 5)
    # Skipped; will be enabled in Phase 6 when scorer provides those fields.

    # Stage 5 — Consensus (emotional bull dominant, concentrated)
    if emotional_bull_ratio > 0.50 and gini_14d < 0.30:
        stage = 5
        conf = base_conf * 0.85

    # Stage 6 — Saturation (emotional bull very dominant, Gini rising)
    if emotional_bull_ratio > 0.65 and gini_14d > 0.55:
        stage = 6
        conf = base_conf * 0.90

    if stage == 0:
        # No rule matched — assign stage 1 as catch-all with low confidence.
        stage = 1
        conf = base_conf * 0.4

    return stage, round(min(1.0, max(0.0, conf)), 4)
