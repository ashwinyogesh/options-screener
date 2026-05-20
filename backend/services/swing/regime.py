"""
Market regime engine for the swing screener.

Computes a global RegimeState from index trend (SPY/QQQ), volatility (^VIX
percentile), small-cap risk appetite (IWM/SPY 20d RS), and breadth (% of
the swing universe trading above its 50-day EMA).

The output is consumed by `services.scoring.swing` and `services.swing_service`
to (a) multiply the composite score by a regime factor, (b) raise the R:R
hard gate when the tape is hostile, and (c) disable certain setups (notably
reversion) in risk-off regimes.

Regime is a *global* property of a scan, not per-symbol. Sector overlay is
handled separately in `swing/sector_map.py`.

Failure mode: if any data fetch fails, returns a NEUTRAL regime — no boost,
no penalty — and logs a warning. The screener degrades gracefully rather than
silently dropping rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from services.data_service import get_ohlc

logger = logging.getLogger(__name__)


# --- VIX percentile bands (1y rolling) ---------------------------------------
VIX_CALM_PCT: float = 25.0       # < 25th percentile → calm
VIX_NORMAL_PCT: float = 60.0     # 25–60th → normal
VIX_ELEVATED_PCT: float = 85.0   # 60–85th → elevated; >85 → shock

# --- Composite risk-on score weights -----------------------------------------
W_INDEX: float = 35.0
W_VOL: float = 25.0
W_BREADTH: float = 25.0
W_RISK_APPETITE: float = 15.0
W_TOTAL: float = W_INDEX + W_VOL + W_BREADTH + W_RISK_APPETITE  # 100

# --- Regime label thresholds (on risk_on_score 0–100) ------------------------
RISK_ON_THRESHOLD: float = 65.0
RISK_OFF_THRESHOLD: float = 40.0

# --- Dynamic R:R gate per regime --------------------------------------------
# NOTE (v2.0.1 calibration): gates must remain ≤ the lowest setup R-multiple
# (`SETUP_R_MULTIPLE` in `services/swing/risk.py`), otherwise setups become
# mathematically impossible to surface — `risk.py` computes
#     target = entry + r_mult × (entry − stop)
# so R:R is identically the setup's R-mult. Reversion = 2.5, momentum = 2.75,
# breakout = 3.0, retest = 3.25. A planned follow-up (decouple target from
# stop with a technical resistance / ATR projection) will let R:R vary
# meaningfully across rows; until then the gates are calibrated to the
# current degenerate behaviour.
RR_GATE_BY_REGIME: dict[str, float] = {
    "risk_on": 2.5,
    "neutral": 2.75,
    "risk_off": 3.0,
}

# --- Composite multiplier curve ---------------------------------------------
# final = composite × (MIN + (MAX - MIN) × risk_on_score / 100)
REGIME_MULT_MIN: float = 0.6
REGIME_MULT_MAX: float = 1.0


@dataclass(slots=True)
class RegimeState:
    index_trend: str             # "bull" | "neutral" | "bear"
    vol_regime: str              # "calm" | "normal" | "elevated" | "shock"
    breadth_pct: float           # 0–100, % of universe > EMA50
    risk_appetite: float         # IWM/SPY 20d RS (≈1.0 = neutral)
    risk_on_score: float         # 0–100 composite
    regime_label: str            # "risk_on" | "neutral" | "risk_off"
    rr_gate: float               # dynamic R:R hard-gate value
    multiplier: float            # composite-score multiplier in [MIN, MAX]
    disable_setups: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    degraded: bool = False       # True if any input failed and we fell back
    spy_close: float = 0.0
    spy_ema21: float = 0.0
    spy_ema50: float = 0.0
    vix: float = 0.0
    vix_percentile: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def _classify_index_trend(close: float, ema21: float, ema50: float) -> tuple[str, float]:
    """Returns (label, score 0–100)."""
    if any(np.isnan(x) for x in (close, ema21, ema50)) or ema50 <= 0:
        return "neutral", 50.0
    if close > ema21 > ema50:
        return "bull", 100.0
    if close < ema21 < ema50:
        return "bear", 0.0
    if close > ema50:
        return "neutral", 65.0
    return "neutral", 35.0


def _classify_vix_regime(vix: float, vix_percentile: float) -> tuple[str, float]:
    """Returns (label, score 0–100). Lower VIX percentile = higher score."""
    if np.isnan(vix) or np.isnan(vix_percentile):
        return "normal", 50.0
    if vix_percentile < VIX_CALM_PCT:
        return "calm", 100.0
    if vix_percentile < VIX_NORMAL_PCT:
        return "normal", 70.0
    if vix_percentile < VIX_ELEVATED_PCT:
        return "elevated", 30.0
    return "shock", 0.0


def _vix_percentile(vix_close: pd.Series) -> tuple[float, float]:
    """Latest VIX value + 252-bar percentile rank."""
    if vix_close is None or len(vix_close) < 30:
        return float("nan"), float("nan")
    latest = float(vix_close.iloc[-1])
    window = vix_close.iloc[-252:] if len(vix_close) >= 252 else vix_close
    rank = float((window <= latest).sum()) / float(len(window)) * 100.0
    return latest, rank


def _compute_breadth(symbol_ohlc: dict[str, pd.DataFrame]) -> float:
    """% of names trading above their 50-bar EMA. Returns 0–100, NaN if empty."""
    if not symbol_ohlc:
        return float("nan")
    above = 0
    total = 0
    for df in symbol_ohlc.values():
        if df is None or len(df) < 50:
            continue
        close = float(df["Close"].iloc[-1])
        ema50 = _ema(df["Close"], 50)
        if np.isnan(ema50):
            continue
        total += 1
        if close > ema50:
            above += 1
    if total == 0:
        return float("nan")
    return round(above / total * 100.0, 1)


def _breadth_score(breadth_pct: float) -> float:
    """Map 0–100 breadth → 0–100 score (linear, capped)."""
    if np.isnan(breadth_pct):
        return 50.0
    return max(0.0, min(100.0, breadth_pct))


def _compute_risk_appetite(iwm_close: pd.Series, spy_close: pd.Series, period: int = 20) -> float:
    """Arithmetic 20d return difference IWM minus SPY.

    Positive = small caps outperforming = risk appetite present.
    Using arithmetic difference (not ratio) so the signal is well-behaved
    when both indexes are negative (crash regime): if IWM falls 15% and SPY
    falls 20%, diff = +5pp (small-caps more resilient), not a spurious 1.06
    ratio that would incorrectly read as risk-on.
    """
    if iwm_close is None or spy_close is None:
        return float("nan")
    if len(iwm_close) < period + 1 or len(spy_close) < period + 1:
        return float("nan")
    iwm_ret = float(iwm_close.iloc[-1]) / float(iwm_close.iloc[-period - 1]) - 1.0
    spy_ret = float(spy_close.iloc[-1]) / float(spy_close.iloc[-period - 1]) - 1.0
    return round(iwm_ret - spy_ret, 4)


def _risk_appetite_score(rs: float) -> float:
    """Map IWM/SPY 20d arithmetic return diff → 0–100.
    -5pp → 0, 0pp → 50, +5pp → 100 (linear)."""
    if np.isnan(rs):
        return 50.0
    if rs <= -0.05:
        return 0.0
    if rs >= 0.05:
        return 100.0
    return (rs + 0.05) / 0.10 * 100.0


def _label_regime(score: float) -> str:
    if score >= RISK_ON_THRESHOLD:
        return "risk_on"
    if score < RISK_OFF_THRESHOLD:
        return "risk_off"
    return "neutral"


def get_vix_context() -> dict[str, float | str | None]:
    """Return current VIX level, 1-year percentile rank, and vol regime label.

    All values are None on data failure — callers must handle Optional fields.
    Safe to call from any router; never raises.
    """
    try:
        vix_df = get_ohlc("^VIX", period="1y")
        vix_val, vix_pct = _vix_percentile(vix_df["Close"])
        vol_label, _ = _classify_vix_regime(vix_val, vix_pct)
        return {
            "vix_level": round(vix_val, 2),
            "vix_percentile": round(vix_pct, 1),
            "vol_regime": vol_label,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_vix_context failed: %s", exc)
        return {"vix_level": None, "vix_percentile": None, "vol_regime": None}


def _disabled_setups(regime_label: str) -> list[str]:
    if regime_label == "risk_off":
        return ["reversion"]
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_regime(
    spy_df: pd.DataFrame | None,
    universe_ohlc: dict[str, pd.DataFrame] | None = None,
) -> RegimeState:
    """Compute the global regime state for the current scan.

    Args:
        spy_df: SPY 1y OHLC, fetched once per scan upstream.
        universe_ohlc: dict of symbol → OHLC DataFrames already fetched for the
            scan. Used to compute breadth without extra network calls. Optional —
            breadth defaults to 50 (neutral) when missing.

    Returns:
        RegimeState. Never raises — degrades to NEUTRAL on partial failure.
    """
    drivers: list[str] = []
    degraded = False

    # --- SPY trend ---
    spy_close = spy_ema21 = spy_ema50 = float("nan")
    if spy_df is not None and len(spy_df) >= 50:
        spy_close = float(spy_df["Close"].iloc[-1])
        spy_ema21 = _ema(spy_df["Close"], 21)
        spy_ema50 = _ema(spy_df["Close"], 50)
        index_label, index_score = _classify_index_trend(spy_close, spy_ema21, spy_ema50)
        drivers.append(f"SPY {index_label} (close {spy_close:.2f} vs EMA21 {spy_ema21:.2f}, EMA50 {spy_ema50:.2f})")
    else:
        index_label, index_score = "neutral", 50.0
        degraded = True
        drivers.append("SPY data unavailable — index trend defaulted to neutral")

    # --- VIX ---
    vix_val = vix_pct = float("nan")
    try:
        vix_df = get_ohlc("^VIX", period="1y")
        vix_val, vix_pct = _vix_percentile(vix_df["Close"])
        vol_label, vol_score = _classify_vix_regime(vix_val, vix_pct)
        drivers.append(f"VIX {vix_val:.1f} ({vix_pct:.0f}p, {vol_label})")
    except Exception as exc:  # noqa: BLE001
        logger.warning("regime: VIX fetch failed: %s", exc)
        vol_label, vol_score = "normal", 50.0
        degraded = True
        drivers.append("VIX data unavailable — vol defaulted to normal")

    # --- IWM/SPY risk appetite ---
    risk_appetite = float("nan")
    try:
        iwm_df = get_ohlc("IWM", period="3mo")
        if spy_df is not None and len(spy_df) >= 21:
            risk_appetite = _compute_risk_appetite(iwm_df["Close"], spy_df["Close"])
        ra_score = _risk_appetite_score(risk_appetite)
        if not np.isnan(risk_appetite):
            drivers.append(f"IWM/SPY 20d RS {risk_appetite:.3f}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("regime: IWM fetch failed: %s", exc)
        ra_score = 50.0
        degraded = True
        drivers.append("IWM data unavailable — risk appetite defaulted to neutral")

    # --- Breadth ---
    breadth_pct = _compute_breadth(universe_ohlc or {})
    breadth_pts = _breadth_score(breadth_pct)
    if np.isnan(breadth_pct):
        drivers.append("breadth not provided — defaulted to neutral")
    else:
        drivers.append(f"breadth {breadth_pct:.0f}% above EMA50")

    # --- Composite ---
    risk_on_score = round(
        (
            W_INDEX * index_score
            + W_VOL * vol_score
            + W_BREADTH * breadth_pts
            + W_RISK_APPETITE * ra_score
        )
        / W_TOTAL,
        2,
    )
    regime_label = _label_regime(risk_on_score)
    multiplier = round(
        REGIME_MULT_MIN + (REGIME_MULT_MAX - REGIME_MULT_MIN) * risk_on_score / 100.0,
        3,
    )
    rr_gate = RR_GATE_BY_REGIME[regime_label]

    return RegimeState(
        index_trend=index_label,
        vol_regime=vol_label,
        breadth_pct=breadth_pct if not np.isnan(breadth_pct) else 0.0,
        risk_appetite=risk_appetite if not np.isnan(risk_appetite) else 0.0,
        risk_on_score=risk_on_score,
        regime_label=regime_label,
        rr_gate=rr_gate,
        multiplier=multiplier,
        disable_setups=_disabled_setups(regime_label),
        drivers=drivers,
        degraded=degraded,
        spy_close=round(spy_close, 2) if not np.isnan(spy_close) else 0.0,
        spy_ema21=round(spy_ema21, 2) if not np.isnan(spy_ema21) else 0.0,
        spy_ema50=round(spy_ema50, 2) if not np.isnan(spy_ema50) else 0.0,
        vix=round(vix_val, 2) if not np.isnan(vix_val) else 0.0,
        vix_percentile=round(vix_pct, 1) if not np.isnan(vix_pct) else 0.0,
    )
