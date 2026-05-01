"""
Unit tests for services.scan_cache.ScanCache and the module-level singletons.

Scenarios covered:
  1. get returns None for a missing key
  2. set → get round-trips the stored value
  3. get returns None after TTL has elapsed
  4. expired get removes the key from _store
  5. clear wipes all entries
  6. module-level singletons are three distinct objects
  7. set overwrites an existing key with the latest value
  8. boundary: expires_at == monotonic() — see NOTE below

NOTE on test 8 (boundary test) — PRODUCTION-CODE BLOCKER:
  The current implementation uses strict `>`:
      if time.monotonic() > entry.expires_at
  So when monotonic() == expires_at the entry is *not* deleted and the value
  is returned.  The desired semantic is that an entry checked at exactly its
  expiry timestamp is already expired.  Test 8 documents that desired
  behaviour (asserts None) and will **fail** against the current code.
  Fix: change `>` to `>=` in services/scan_cache.py.
"""
from __future__ import annotations

from unittest.mock import patch

from services.scan_cache import (
    ScanCache,
    cc_scan_cache,
    csp_scan_cache,
    ditm_scan_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh() -> ScanCache:
    """Return a brand-new ScanCache with a 60-second TTL."""
    return ScanCache(ttl=60)


# ---------------------------------------------------------------------------
# 1 — missing key
# ---------------------------------------------------------------------------

def test_get_returns_none_for_missing_key():
    # Arrange
    cache = _fresh()

    # Act
    result = cache.get("nonexistent")

    # Assert
    assert result is None


# ---------------------------------------------------------------------------
# 2 — set / get round-trip
# ---------------------------------------------------------------------------

def test_set_then_get_returns_value():
    # Arrange
    cache = _fresh()
    payload = {"ticker": "AAPL", "score": 75}

    # Act
    cache.set("aapl", payload)
    result = cache.get("aapl")

    # Assert
    assert result == payload


# ---------------------------------------------------------------------------
# 3 — get returns None after TTL elapsed
# ---------------------------------------------------------------------------

def test_get_returns_none_after_ttl_expires():
    # Arrange
    cache = _fresh()
    with patch("time.monotonic") as mock_mono:
        mock_mono.return_value = 1000.0
        cache.set("key", "value")  # expires_at = 1060.0

        # Act — advance clock 61 s past set time (1 s beyond TTL)
        mock_mono.return_value = 1061.0
        result = cache.get("key")

    # Assert
    assert result is None


# ---------------------------------------------------------------------------
# 4 — expired get removes the key from _store
# ---------------------------------------------------------------------------

def test_get_deletes_expired_entry():
    # Arrange
    cache = _fresh()
    with patch("time.monotonic") as mock_mono:
        mock_mono.return_value = 1000.0
        cache.set("stale", "stale_value")  # expires_at = 1060.0

        # Act — trigger expiry
        mock_mono.return_value = 1065.0
        cache.get("stale")

    # Assert — key must be gone from the internal store
    assert "stale" not in cache._store


# ---------------------------------------------------------------------------
# 5 — clear wipes all entries
# ---------------------------------------------------------------------------

def test_clear_removes_all_entries():
    # Arrange
    cache = _fresh()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)

    # Act
    cache.clear()

    # Assert
    assert cache.get("a") is None
    assert cache.get("b") is None
    assert cache.get("c") is None


# ---------------------------------------------------------------------------
# 6 — module-level singletons are distinct objects
# ---------------------------------------------------------------------------

def test_singletons_are_distinct_instances():
    # Arrange / Act — singletons initialised at import time

    # Assert
    assert csp_scan_cache is not cc_scan_cache
    assert cc_scan_cache is not ditm_scan_cache
    assert csp_scan_cache is not ditm_scan_cache


# ---------------------------------------------------------------------------
# 7 — set overwrites existing key
# ---------------------------------------------------------------------------

def test_set_overwrites_existing_key():
    # Arrange
    cache = _fresh()
    cache.set("key", "first")

    # Act
    cache.set("key", "second")
    result = cache.get("key")

    # Assert
    assert result == "second"


# ---------------------------------------------------------------------------
# 8 — boundary: expires_at == monotonic() is treated as expired
#
# PRODUCTION-CODE BLOCKER — see module docstring.
# This test will FAIL against the current code (strict `>` comparison).
# ---------------------------------------------------------------------------

def test_get_does_not_return_entry_that_expires_at_exactly_now():
    # Arrange
    cache = _fresh()
    with patch("time.monotonic") as mock_mono:
        mock_mono.return_value = 1000.0
        cache.set("boundary", "value")  # expires_at = 1060.0

        # Act — check at exactly the expiry timestamp
        mock_mono.return_value = 1060.0
        result = cache.get("boundary")

    # Assert — entry checked at its exact expiry timestamp should be expired
    assert result is None
