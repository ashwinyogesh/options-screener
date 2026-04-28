"""
Screener orchestration package.

Phase 2 (this commit) introduces only the **type surface** — `ScreenerConfig`
and the protocol types in `types`. No service code is wired through these yet;
that lands in Phase 3 (`runner.py`) and Phase 4 (per-screener configs).

Public API:
    from services.screener import ScreenerConfig, Indicators, StrikeContext, ...
"""
from __future__ import annotations

from .config import ScreenerConfig
from .types import (
    BaseScreenerResult,
    BaseStrikeResult,
    ChainFetcher,
    DeltaFn,
    Direction,
    EnvScorer,
    GateResult,
    HardGate,
    Indicators,
    IvLookup,
    OhlcFetcher,
    PreProcessor,
    ResultFactory,
    StrikeBuildInputs,
    StrikeContext,
    StrikeContextBuilder,
    StrikeFilter,
    StrikeScorer,
    SymbolFactory,
    SymbolMetrics,
    TieBreakKey,
)

__all__ = [
    "ScreenerConfig",
    "BaseScreenerResult",
    "BaseStrikeResult",
    "ChainFetcher",
    "DeltaFn",
    "Direction",
    "EnvScorer",
    "GateResult",
    "HardGate",
    "Indicators",
    "IvLookup",
    "OhlcFetcher",
    "PreProcessor",
    "ResultFactory",
    "StrikeBuildInputs",
    "StrikeContext",
    "StrikeContextBuilder",
    "StrikeFilter",
    "StrikeScorer",
    "SymbolFactory",
    "SymbolMetrics",
    "TieBreakKey",
]
