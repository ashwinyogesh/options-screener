"""ACS scorer (Phase 6).

Phase 0 stub. Phase 6 PR fills in the 15-min cron scorer that reads
acs-component-weights from Key Vault and writes acs_scores to Postgres,
then refreshes Redis caches per docs/NARRATIVE_METHODOLOGY.md §5 + §8.
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger(__name__).info(
        "narrative-scorer: Phase 0 stub. See docs/NARRATIVE_METHODOLOGY.md §8 (Phase 6)."
    )


if __name__ == "__main__":
    main()
