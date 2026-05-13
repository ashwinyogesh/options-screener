"""Ticker extraction worker (Phase 2).

Phase 0 stub: logs and exits cleanly so the image builds and the Container
Apps Job can be provisioned. Phase 2 PR replaces this with the actual
Layer 1–5 extraction pipeline per docs/NARRATIVE_METHODOLOGY.md §3.
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger(__name__).info(
        "narrative-extractor: Phase 0 stub. See docs/NARRATIVE_METHODOLOGY.md §8 (Phase 2)."
    )


if __name__ == "__main__":
    main()
