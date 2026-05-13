"""Domain exceptions for the narrative service layer.

Services raise these; routers translate them to HTTP responses. Services must
not import FastAPI types — see ADR-0013 and copilot-instructions.md.
"""
from __future__ import annotations


class NarrativeError(Exception):
    """Base class for narrative-domain errors."""


class NarrativeUnavailable(NarrativeError):
    """Raised when the narrative platform is not yet provisioned (Phases 1–6 pending)."""


class TickerNotTracked(NarrativeError):
    """Raised when a ticker has no narrative history (no mentions in 14d)."""


class NarrativeNotFound(NarrativeError):
    """Raised when a narrative cluster ID does not resolve."""
