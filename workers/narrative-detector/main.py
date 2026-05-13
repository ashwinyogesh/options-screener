"""Narrative detector / clusterer (Phase 5).

Phase 0 stub. Phase 5 PR fills in HDBSCAN clustering on pgvector embeddings
+ lifecycle stage assignment per docs/NARRATIVE_METHODOLOGY.md §4 + §8.
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger(__name__).info(
        "narrative-detector: Phase 0 stub. See docs/NARRATIVE_METHODOLOGY.md §8 (Phase 5)."
    )


if __name__ == "__main__":
    main()
