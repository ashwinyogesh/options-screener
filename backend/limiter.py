"""Shared rate limiter instance.

Imported by main.py and routers. slowapi requires a single Limiter
instance shared across the app; this module provides it.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Default per-IP limits applied to every route.
# Stricter limits for LLM-heavy routes are applied via @limiter.limit
# decorators in those routers.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute", "600/hour"],
)
