"""Attention aggregator (Phase 3).

Phase 0 stub. Phase 3 PR fills in the 15-min cron writer for ticker_timeline
per docs/NARRATIVE_METHODOLOGY.md §2 + §8.
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger(__name__).info(
        "narrative-aggregator: Phase 0 stub. See docs/NARRATIVE_METHODOLOGY.md §8 (Phase 3)."
    )


if __name__ == "__main__":
    main()
