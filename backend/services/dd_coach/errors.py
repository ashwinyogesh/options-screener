"""Domain exceptions for the DD Coach service.

Routers map these to HTTP status codes — services never import FastAPI types.
"""
from __future__ import annotations


class DDCoachError(Exception):
    """Base for all DD Coach domain errors."""


class DDCoachUnavailable(DDCoachError):
    """Backing store (Cosmos) is not configured or unreachable.

    Maps to HTTP 503.
    """


class DDEntryNotFound(DDCoachError):
    """Requested entry does not exist (or wrong partition key).

    Maps to HTTP 404.
    """


class DDEntryImmutable(DDCoachError):
    """Attempted to mutate or delete a completed (immutable) entry.

    Maps to HTTP 409 (Conflict).
    """


class DDEntryInvalid(DDCoachError):
    """Validation failure beyond schema (e.g., completing a draft missing
    required answers).

    Maps to HTTP 422.
    """
