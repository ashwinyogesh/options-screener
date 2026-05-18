"""Configuration for the ingestion worker.

Env contract (set by Container Apps):
- KEYVAULT_URI         e.g. https://kv-narrative-<suffix>.vault.azure.net/
- EVENT_HUB_NAMESPACE  e.g. evhns-narrative-<suffix>.servicebus.windows.net
- BLOB_ACCOUNT_NAME    e.g. stnarrative<suffix>
- REDDIT_USER_AGENT    User-Agent sent to Arctic Shift API (default provided)
- LOG_LEVEL            INFO / DEBUG (default INFO)

Secret contract (Key Vault, fetched once at startup):
- reddit-author-salt   used for SHA-256(username + salt). Never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Reddit requires: platform:app_id:version (by /u/username)
# Override REDDIT_USER_AGENT env var to set your Reddit username.
_DEFAULT_UA = "script:narrative-screener:1.0 (by /u/AshwinChandlapur)"


@dataclass(frozen=True)
class WorkerConfig:
    keyvault_uri: str
    event_hub_namespace: str
    blob_account_name: str
    log_level: str
    reddit_user_agent: str = _DEFAULT_UA
    raw_events_hub: str = "reddit-raw-events"
    blob_container: str = "reddit-raw"
    poll_interval_seconds: int = 60
    reddit_rate_limit_per_min: int = 60  # Arctic Shift soft cap; bumped from 30 to accommodate 28 subreddits


def load_from_env() -> WorkerConfig:
    return WorkerConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        event_hub_namespace=_required("EVENT_HUB_NAMESPACE"),
        blob_account_name=_required("BLOB_ACCOUNT_NAME"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        reddit_user_agent=os.getenv("REDDIT_USER_AGENT", _DEFAULT_UA),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value


# Subreddit tiers per docs/NARRATIVE_METHODOLOGY.md §4.
# Stored in code (not Key Vault) so PRs are reviewable; tier membership is a
# methodology decision, not a secret.
#
# Tier 1 — analyst-grade communities: deep DD, fundamental analysis, low noise.
# Tier 2 — active trading communities: high volume, mixed quality, strong signal on momentum.
# Tier 3 — thematic communities: sector/technology focus; useful for leading sentiment
#           on specific AI-layer, space, or macro plays.
SUBREDDIT_TIERS: dict[str, list[str]] = {
    "tier1": [
        # Broad investing / analysis
        "investing", "stocks", "SecurityAnalysis", "ValueInvesting", "Bogleheads",
        # Systematic / quant traders — cite specific tickers and setups
        "algotrading",
        # Macro context that drives sector rotations
        "Economics",
    ],
    "tier2": [
        # Retail momentum
        "wallstreetbets", "options", "smallstreetbets", "pennystocks",
        "TheRaceTo10Million", "swingtrading",
        # Space stocks — RKLB, ASTS, LUNR, RDW, SPCE
        "spacestocks", "SpaceXLounge",
        # AI Chips — NVDA-specific community, high volume
        "nvidia",
        # AI Models — most active LLM community; discusses NVDA, MSFT, GOOG, META model bets
        "LocalLLaMA",
        # AI Applications — PLTR-specific; enterprise AI plays
        "Palantir",
    ],
    "tier3": [
        # Existing thematic
        "artificial", "SemiConductors", "energy", "biotech", "space", "geopolitics",
        # AI Energy layer — nuclear renaissance plays: CEG, VST, NuScale, Oklo, SMR
        "nuclear",
        # AI Infra layer — cloud hyperscaler sentiment: AMZN/AWS, MSFT/Azure, GOOG
        "CloudComputing",
        # AI Models layer (academic/practitioner) — leading indicator for model company sentiment
        "MachineLearning",
        # AI Applications layer — SaaS companies benefiting from AI integration
        "SaaS",
    ],
}


def all_subreddits() -> list[str]:
    return [s for tier in SUBREDDIT_TIERS.values() for s in tier]
