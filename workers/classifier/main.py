"""Conviction-state classifier (Phase 4).

Phase 0 stub. Phase 4 PR fills in the gpt-4o-mini structured-output
classifier per docs/NARRATIVE_METHODOLOGY.md §3 + §8.
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger(__name__).info(
        "narrative-classifier: Phase 0 stub. See docs/NARRATIVE_METHODOLOGY.md §8 (Phase 4)."
    )


if __name__ == "__main__":
    main()
