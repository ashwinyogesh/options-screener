"""Postgres connection pool for narrative routes.

Phase 0 stub: pool is None and `get_pool()` raises NarrativeUnavailable.
Phase 1 wires asyncpg with the connection string from Key Vault env var
`NARRATIVE_PG_CONN`. Pool is shared across all narrative routes per the
service-layering rule.

Lifespan management: created on FastAPI startup, closed on shutdown. Until
Phase 1 lands, no pool is created, so the existing /api/screener/* routes are
unaffected.
"""
from __future__ import annotations

import os
from typing import Any

from services.narrative.errors import NarrativeUnavailable

# Phase 1+: this becomes asyncpg.Pool.
_pool: Any | None = None


async def init_pool() -> None:
    """Create the asyncpg pool. Called on FastAPI startup in Phase 1."""
    global _pool
    if _pool is not None:
        return
    conn_str = os.getenv("NARRATIVE_PG_CONN")
    if not conn_str:
        # No connection string yet — leave pool as None. Routes will 503.
        return
    # Phase 1 implementation:
    #   import asyncpg
    #   _pool = await asyncpg.create_pool(conn_str, min_size=1, max_size=5)
    raise NarrativeUnavailable(
        "asyncpg integration not yet wired (Phase 1). "
        "Set NARRATIVE_PG_CONN and land Phase 1 PR.",
    )


async def close_pool() -> None:
    """Close the asyncpg pool on FastAPI shutdown."""
    global _pool
    if _pool is None:
        return
    # Phase 1 implementation:
    #   await _pool.close()
    _pool = None


def get_pool() -> Any:
    """Return the active pool. Raises NarrativeUnavailable until Phase 1 lands."""
    if _pool is None:
        raise NarrativeUnavailable(
            "Narrative Postgres pool not initialized. "
            "This is expected until Phase 1 deploys the platform.",
        )
    return _pool
