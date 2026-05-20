"""
In-memory TTL cache for expensive scan results.

As of ADR-0024, this cache is used exclusively by the custom-list POST
endpoints (/csp, /cc, /ditm, /ditm). The universe GET /scan endpoints read
from the precomputed Cosmos containers (screener_csp, screener_cc,
screener_ditm) instead, which are durable across process restarts.

One module-level ScanCache instance per strategy. The routers call
``get`` before running the custom-list scan and ``set`` after — no other
module should need to import this.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_TTL_SECONDS = 1800  # 30 minutes

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class ScanCache:
    """Thread-safe-enough dict cache (asyncio single-thread model)."""

    def __init__(self, ttl: int = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._store: dict[str, _Entry[Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)

    def clear(self) -> None:
        self._store.clear()


# One singleton per strategy — imported by the routers.
csp_scan_cache: ScanCache = ScanCache()
cc_scan_cache: ScanCache = ScanCache()
ditm_scan_cache: ScanCache = ScanCache()
swing_scan_cache: ScanCache = ScanCache()
# NOTE: a shared ``regime_cache`` singleton lived here prior to Phase-1
# cleanup. It was a single-key, process-global memoization of the swing
# market regime used as a side-channel between ``swing_service.run_scan``
# and its callers. It is gone — ``run_scan`` now returns the regime
# explicitly. Do not reintroduce a global regime cache; if you need
# per-scan memoization, key it on ``(as_of, universe_hash)`` and scope it
# to the request, not the process.
