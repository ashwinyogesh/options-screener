"""
Curated stock universe for the Momentum screener auto-scan.
~115 liquid, high-momentum names across AI, semis, cloud, fintech, and growth,
with explicit AI-buildout coverage (energy, chips, infrastructure, models, apps).
"""
from __future__ import annotations

# AI buildout tickers grouped by where they sit in the stack.
# These are merged into MOMENTUM_UNIVERSE below (deduped, order-preserving).
AI_BUILDOUT: dict[str, list[str]] = {
    # Powering the datacenters: nuclear, gas/coal merchant power, grid build,
    # power management, datacenter cooling, uranium fuel cycle.
    "energy": [
        "VST", "CEG", "NRG", "TLN", "NEE", "ETR", "DUK", "SO", "EXC",
        "OKLO", "SMR", "BWXT", "CCJ",
        "GEV", "ETN", "VRT", "PWR", "FSLR",
    ],
    # Silicon, foundry, equipment, materials, optics, connectivity.
    "chips": [
        "NVDA", "AMD", "AVGO", "QCOM", "MRVL", "ARM", "MU", "INTC",
        "TSM", "ASML", "AMAT", "LRCX", "KLAC", "TXN", "ON", "MPWR",
        "NXPI", "ADI", "MCHP", "WOLF",
        "ALAB", "CRDO", "COHR", "LITE",
    ],
    # Servers, networking, storage, datacenter REITs, GPU-cloud operators.
    "infrastructure": [
        "SMCI", "DELL", "HPE", "IBM", "CSCO",
        "ANET", "JNPR", "CIEN",
        "NTAP", "PSTG",
        "EQIX", "DLR", "IRM",
        "NBIS", "CRWV", "IREN",
    ],
    # Public companies with material foundation-model exposure.
    "models": [
        "MSFT", "GOOGL", "META", "AMZN", "AAPL", "TSLA",
        "BIDU", "BABA",
    ],
    # Companies monetizing AI inside their product (data, security, dev, vertical).
    "applications": [
        "PLTR", "CRWD", "NET", "SNOW", "DDOG", "ZS", "PANW", "NOW",
        "CRM", "ORCL", "WDAY", "HUBS", "MDB", "APP", "GTLB", "CFLT",
        "ADBE", "INTU", "TEAM", "DUOL", "S", "BILL",
        "AI", "SOUN",
        "RXRX", "ISRG",
        "RDDT", "RBLX",
    ],
}

# Non-AI core (kept from prior universe).
_NON_AI_CORE: list[str] = [
    # Fintech / crypto-adjacent
    "COIN", "HOOD", "SQ", "AFRM", "SOFI", "MSTR", "PYPL",
    # Growth / consumer tech
    "SHOP", "UBER", "ABNB",
    # Quantum / space
    "IONQ", "RGTI", "ACHR",
    # Healthcare growth
    "LLY", "MRNA", "HIMS",
    # Sector ETFs
    "QQQ", "SOXX", "SMH",
]

# Diversified core: 45 large-cap names across 8 non-tech sectors. Added
# 2026-05 per ADR-0011 universe-expansion validation (n=158, Spearman
# rho of CSP final_score vs realised ROC improves from +0.475 to +0.486 —
# Method D generalises off the momentum tickers). All names: liquid options
# chains, regular earnings cadence, multiple market-makers.
_DIVERSIFIED_CORE: dict[str, list[str]] = {
    "financials":   ["JPM", "BAC", "GS", "MS", "V", "MA", "SCHW", "BLK"],
    "staples":      ["KO", "PG", "PEP", "COST", "WMT", "MO", "MDLZ"],
    "healthcare":   ["JNJ", "UNH", "ABT", "MRK", "PFE", "ABBV", "TMO"],
    "industrials":  ["CAT", "DE", "HON", "RTX", "LMT", "UNP"],
    "energy":       ["XOM", "CVX", "COP", "EOG", "SLB"],
    "materials":    ["LIN", "FCX", "NEM", "NUE"],
    "real_estate": ["PLD", "AMT", "SPG", "O"],
    "consumer_disc":["HD", "LOW", "NKE", "MCD"],
}

# Stable CSP universe: large-cap names with liquid option chains, tight put spreads,
# RSI that spends meaningful time in the 42–62 sweet spot, and IV/HV closer to 1.1–1.3.
# Ideal for traders with limited capital (≤ $20K per contract) who need tradeable CSP signals.
_STABLE_CSP: list[str] = [
    # Large-cap financials — tight put spreads, event premium, RSI stability
    "JPM", "BAC", "GS", "MS", "WFC", "C",
    # Payment networks — deep chains, consistent IV/HV premium
    "V", "MA",
    # Consumer defensive / staples — best RSI stability, low gap risk
    "WMT", "COST", "KO", "PG", "MCD",
    # Industrials — uncorrelated to AI sentiment rotation, own vol cycle
    "CAT", "DE", "GE", "HON", "RTX",
    # Healthcare large-cap — liquid chains, low beta relative to tech
    "JNJ", "UNH", "ABT",
    # Home improvement / retail — liquid, stable trend
    "HD", "LOW",
]


def _build_universe() -> list[str]:
    """Flatten AI buckets + core + diversified, preserving order, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for bucket in ("energy", "chips", "infrastructure", "models", "applications"):
        for sym in AI_BUILDOUT[bucket]:
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
    for sym in _NON_AI_CORE:
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    for bucket in ("financials", "staples", "healthcare", "industrials",
                   "energy", "materials", "real_estate", "consumer_disc"):
        for sym in _DIVERSIFIED_CORE[bucket]:
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


MOMENTUM_UNIVERSE: list[str] = _build_universe()
UNIVERSE_SIZE: int = len(MOMENTUM_UNIVERSE)


def _ai_full() -> list[str]:
    """All AI buckets combined, order-preserving deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for bucket in ("energy", "chips", "infrastructure", "models", "applications"):
        for sym in AI_BUILDOUT[bucket]:
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


# Swing screener universe — pre-vetted liquid mid/large caps suitable for
# directional 3–21 day equity setups. All names: ≥ $500M market cap, ≥ 500K ADV.
# Statically curated; no algorithmic filtering. See ADR-0009.
_SWING_ELIGIBLE: list[str] = [
    # Mega-cap tech (always liquid, deep options chains for sentiment)
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "AVGO",
    # Semis
    "AMD", "QCOM", "MRVL", "ARM", "MU", "INTC", "TSM", "ASML", "AMAT", "LRCX",
    "KLAC", "TXN", "ON", "MPWR", "NXPI", "ADI", "MCHP", "ALAB", "CRDO",
    # Cloud / SaaS / AI apps
    "ORCL", "CRM", "NOW", "WDAY", "ADBE", "INTU", "PANW", "CRWD", "NET", "DDOG",
    "SNOW", "ZS", "MDB", "TEAM", "HUBS", "APP", "PLTR", "GTLB", "CFLT", "S",
    "BILL", "DUOL", "AI",
    # Hardware / infra
    "SMCI", "DELL", "HPE", "IBM", "CSCO", "ANET", "JNPR", "CIEN", "NTAP", "PSTG",
    "VRT", "ETN", "GEV",
    # Financials (Large + reasonable beta for swings)
    "JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "BLK", "AXP", "V", "MA",
    "PYPL", "COIN", "HOOD", "SQ", "AFRM", "SOFI", "MSTR",
    # Industrials
    "CAT", "DE", "GE", "HON", "RTX", "LMT", "NOC", "BA", "PWR", "URI",
    # Energy / AI buildout power
    "VST", "CEG", "NRG", "TLN", "NEE", "ETR", "DUK", "SO", "EXC", "OKLO",
    "SMR", "BWXT", "CCJ", "XOM", "CVX", "OXY", "EOG", "MPC", "SLB", "FSLR",
    # Consumer / retail
    "WMT", "COST", "TGT", "HD", "LOW", "NKE", "LULU", "MCD", "SBUX", "CMG",
    "DIS", "NFLX", "ROKU", "SPOT",
    # Travel / mobility
    "UBER", "ABNB", "DASH", "BKNG", "DAL", "UAL", "LUV", "CCL", "RCL",
    # Healthcare / biotech (liquid only)
    "JNJ", "UNH", "ABT", "LLY", "MRK", "PFE", "BMY", "TMO", "ISRG", "MRNA",
    "HIMS", "DXCM", "VRTX", "REGN",
    # Materials / mining
    "FCX", "NEM", "AA", "X", "CLF",
    # China ADRs
    "BABA", "BIDU", "PDD", "JD", "NIO", "LI", "XPEV",
    # Speculative momentum
    "RBLX", "RDDT", "SHOP", "BROS", "CRWV", "NBIS", "IREN", "RIOT", "MARA",
    "IONQ", "RGTI", "ACHR", "RKLB", "JOBY", "LCID", "RIVN",
    # High-beta / high-alpha movers (liquid options, strong swing range)
    "ASTS", "QBTS", "BBAI", "TEM", "SERV", "PATH", "AUR", "HIVE",
    "WULF", "CLSK", "BTDR", "APLD", "GLXY",
    # ETFs for sector pair-trade context
    "QQQ", "SPY", "IWM", "SOXX", "SMH", "XLE", "XLF", "XLK", "XLV",
]
# Dedup, order-preserving.
SWING_UNIVERSE: list[str] = list(dict.fromkeys(_SWING_ELIGIBLE))


# Selectable scan universes exposed via the API. Keys are stable identifiers
# the frontend sends as ?universe=...; values are the resolved ticker lists.
UNIVERSES: dict[str, list[str]] = {
    "all": MOMENTUM_UNIVERSE,
    "ai_full": _ai_full(),
    "ai_energy": list(AI_BUILDOUT["energy"]),
    "ai_chips": list(AI_BUILDOUT["chips"]),
    "ai_infrastructure": list(AI_BUILDOUT["infrastructure"]),
    "ai_models": list(AI_BUILDOUT["models"]),
    "ai_applications": list(AI_BUILDOUT["applications"]),
    "stable_csp": _STABLE_CSP,
    "diversified": [s for bucket in ("financials", "staples", "healthcare", "industrials",
                                       "energy", "materials", "real_estate", "consumer_disc")
                       for s in _DIVERSIFIED_CORE[bucket]],
    "swing_eligible": SWING_UNIVERSE,
}


def get_universe(name: str | None) -> tuple[str, list[str]]:
    """Resolve a universe name to (canonical_name, ticker_list). Defaults to 'all'."""
    key = (name or "all").lower()
    if key not in UNIVERSES:
        key = "all"
    return key, UNIVERSES[key]
