"""Persistence layer for SEC companyfacts payloads.

The `FundamentalsCache` Protocol is the contract; today we ship one
implementation (`DiskFundamentalsCache`). A future Cosmos-backed implementation
will plug in here without touching `services.fundamentals_service`.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# A minimum size below which we treat a cached payload as corrupt / placeholder.
_MIN_BYTES = 1000


class FundamentalsCache(Protocol):
    """Storage interface for raw companyfacts payloads.

    All methods are synchronous; callers that need async should wrap in
    `asyncio.to_thread`.
    """

    def get_cik_map(self) -> dict[str, str] | None: ...

    def put_cik_map(self, mapping: dict[str, str]) -> None: ...

    def get_companyfacts(self, ticker: str) -> dict[str, Any] | None:
        """Return the cached payload, or None if absent / expired."""
        ...

    def put_companyfacts(self, ticker: str, payload: dict[str, Any]) -> None: ...

    def age_seconds(self, ticker: str) -> int | None:
        """How long ago (in seconds) the payload was written. None if absent."""
        ...


# ---------------------------------------------------------------------------
# Disk-backed implementation
# ---------------------------------------------------------------------------

class DiskFundamentalsCache:
    """Stores payloads as plain JSON under `root_dir`.

      root_dir/
        cik_map.json
        AAPL.json
        MSFT.json
        ...

    Concurrency: not safe for multi-writer workloads. Writes use atomic
    rename so partial reads don't see truncated files.
    """

    def __init__(self, root_dir: Path | str) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- cik_map
    def get_cik_map(self) -> dict[str, str] | None:
        path = self.root_dir / "cik_map.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("cik_map.json unreadable: %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        return {str(k).upper(): str(v) for k, v in data.items()}

    def put_cik_map(self, mapping: dict[str, str]) -> None:
        self._atomic_write(self.root_dir / "cik_map.json", json.dumps(mapping))

    # -------------------------------------------------------- companyfacts
    def _facts_path(self, ticker: str) -> Path:
        return self.root_dir / f"{ticker.upper()}.json"

    def get_companyfacts(self, ticker: str) -> dict[str, Any] | None:
        path = self._facts_path(ticker)
        if not path.exists() or path.stat().st_size < _MIN_BYTES:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("companyfacts cache read failed for %s: %s", ticker, exc)
            return None

    def put_companyfacts(self, ticker: str, payload: dict[str, Any]) -> None:
        self._atomic_write(self._facts_path(ticker), json.dumps(payload))

    def age_seconds(self, ticker: str) -> int | None:
        path = self._facts_path(ticker)
        if not path.exists():
            return None
        return int(time.time() - path.stat().st_mtime)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _atomic_write(target: Path, body: str) -> None:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(body)
        tmp.replace(target)
