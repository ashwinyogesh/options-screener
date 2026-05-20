"""
Swing-trade risk model — per-setup entry/stop/target geometry.

Each setup has a structural ENTRY TRIGGER — the price at which the trade
actually activates — rather than naively executing at the latest close:

  breakout   : trigger = base_high            ; stop = base_low − 0.5·ATR
  momentum   : trigger = EMA8 (pullback)       ; stop = swing_low or EMA21 − 1·ATR
  reversion  : trigger = current price         ; stop = swing_low − 0.25·ATR
  retest     : trigger = reclaimed level       ; stop = level − 0.5·ATR (or swing_low)

If features required by a setup's trigger are missing, the model falls
back to current_price + ATR stop (the v1.0 behavior).

R:R is computed off the **trigger entry**, not the current price — this
captures the trade you would actually take if you waited for the proper
entry. The `extended` flag warns when current_price has already run more
than ~3% past the trigger; the setup may still be valid, but you would
be chasing rather than entering at the structural level.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Setup-specific minimum R-multiples (used as the floor guarantee).
# R:R is computed from a technical target (ATR projection), clipped to at least
# r_mult × risk so that every setup still meets its structural minimum.
SETUP_R_MULTIPLE: dict[str, float] = {
    "breakout": 3.0,
    "momentum": 2.75,
    "reversion": 2.5,
    "retest": 3.25,
}

# Setup-specific holding window in trading days.
SETUP_HOLD_DAYS: dict[str, tuple[int, int]] = {
    "breakout": (5, 10),
    "momentum": (7, 14),
    "reversion": (3, 7),
    "retest": (10, 21),
}

# ATR-projection multipliers for technical target computation (v2.3.0).
# target = min(entry + ATR_TARGET_MULT[setup] × atr14, entry + r_mult × risk).
# ATR projection is the credibility ceiling: if the stop is so wide that the
# R-multiple floor would require a move beyond ATR capacity, the ATR target wins
# and R:R is accepted as-is (likely below gate → setup filtered). This prevents
# phantom targets on wide-stop setups like large-cap names with base-low stops.
ATR_TARGET_MULT: dict[str, float] = {
    "breakout": 3.0,
    "momentum": 2.5,
    "reversion": 2.0,
    "retest": 3.5,
}

ATR_STOP_MULT: float = 1.5
RR_HARD_GATE: float = 2.5
EXTENDED_PCT: float = 0.03  # current_price > trigger * (1 + EXTENDED_PCT) → chasing


@dataclass(slots=True)
class SetupTrigger:
    entry: float
    stop: float
    kind: str  # "break_above" | "pullback_to_ema8" | "reclaim_confirm" | "retest_of"
    extended: bool


@dataclass(slots=True)
class RiskPlan:
    entry: float
    stop: float
    target: float
    risk_per_share: float
    reward_per_share: float
    rr: float
    hold_min_days: int
    hold_max_days: int
    passes_gate: bool
    current_price: float = 0.0
    trigger_kind: str = ""
    extended: bool = False
    target_method: str = ""  # "atr_projection" | "rr_floor" — which determined target


def _atr_fallback_stop(current_price: float, atr14: float, recent_swing_low: float) -> float:
    """Tighter of ATR-stop and swing low, never above entry."""
    atr_stop = current_price - ATR_STOP_MULT * atr14
    stop = max(atr_stop, recent_swing_low) if recent_swing_low > 0 else atr_stop
    return atr_stop if stop >= current_price else stop


def build_trigger(
    setup: str,
    current_price: float,
    atr14: float,
    recent_swing_low: float,
    features: dict[str, Any] | None,
) -> SetupTrigger:
    """Derive entry trigger + structural stop for the given setup.

    Falls back to (current_price, ATR-stop) when features required by the
    setup are missing or degenerate.
    """
    f = features or {}

    if setup == "breakout":
        base = f.get("consolidation_base") or {}
        base_high = base.get("base_high")
        base_low = base.get("base_low")
        if base_high and base_high == base_high and base_low and base_low == base_low and base_high > 0:
            stop = base_low - 0.5 * atr14
            entry = float(base_high)
            extended = current_price > entry * (1 + EXTENDED_PCT)
            return SetupTrigger(entry=entry, stop=stop, kind="break_above", extended=extended)

    elif setup == "momentum":
        ema = f.get("ema_alignment") or {}
        ema8 = ema.get("ema8")
        ema21 = ema.get("ema21")
        if ema8 and ema8 == ema8 and ema8 > 0:
            entry = float(ema8)
            # Stop: tighter of (EMA21 − 1·ATR) and swing low
            ema_stop = (float(ema21) - atr14) if (ema21 and ema21 == ema21) else 0.0
            stop = max(ema_stop, recent_swing_low) if recent_swing_low > 0 else ema_stop
            if stop <= 0 or stop >= entry:
                stop = entry - ATR_STOP_MULT * atr14
            extended = current_price > entry * (1 + EXTENDED_PCT)
            return SetupTrigger(entry=entry, stop=stop, kind="pullback_to_ema8", extended=extended)

    elif setup == "retest":
        reclaim = f.get("structure_reclaim") or {}
        level = reclaim.get("level")
        if reclaim.get("reclaimed") and level and level == level and level > 0:
            entry = float(level)
            # Stop: 0.5·ATR below the level, or recent swing low if tighter
            atr_stop = entry - 0.5 * atr14
            stop = max(atr_stop, recent_swing_low) if recent_swing_low > 0 and recent_swing_low < entry else atr_stop
            extended = current_price > entry * (1 + EXTENDED_PCT * 1.3)  # retests get a wider tolerance
            return SetupTrigger(entry=entry, stop=stop, kind="retest_of", extended=extended)

    elif setup == "reversion":
        # Reversion entries are here-and-now by nature; tighter stop under swing low.
        if recent_swing_low > 0 and recent_swing_low < current_price:
            stop = recent_swing_low - 0.25 * atr14
        else:
            stop = current_price - ATR_STOP_MULT * atr14
        # Extended if price has already bounced significantly above EMA8 (most of the bounce played).
        ema = f.get("ema_alignment") or {}
        ema8 = ema.get("ema8")
        extended = bool(ema8 and ema8 == ema8 and current_price > float(ema8) * 1.02)
        return SetupTrigger(entry=current_price, stop=stop, kind="reclaim_confirm", extended=extended)

    # Fallback — old v1.0 behavior
    return SetupTrigger(
        entry=current_price,
        stop=_atr_fallback_stop(current_price, atr14, recent_swing_low),
        kind="market_close",
        extended=False,
    )


def build_risk_plan(
    setup: str,
    current_price: float,
    atr14: float,
    recent_swing_low: float,
    features: dict[str, Any] | None = None,
) -> RiskPlan:
    """
    Build a per-setup risk plan.

    R:R is computed using the **trigger entry** — the structural price at
    which the setup activates — not necessarily current_price. The trigger
    is setup-specific (base_high for breakout, EMA8 for momentum, reclaimed
    level for retest, current_price for reversion).

    `extended=True` flags rows where current_price has already moved more
    than ~3% past the trigger; the setup is still scored at face value, but
    a real-world entry would either chase (degrading the actual R:R) or
    wait for a pullback.
    """
    setup = setup if setup in SETUP_R_MULTIPLE else "breakout"
    r_mult = SETUP_R_MULTIPLE[setup]
    hold_min, hold_max = SETUP_HOLD_DAYS[setup]

    trig = build_trigger(setup, current_price, atr14, recent_swing_low, features)
    entry = trig.entry
    stop = trig.stop
    risk = entry - stop

    # Sanity: stop must be below entry and risk ≤ 50% of entry.
    if risk <= 0 or risk > entry * 0.5:
        return RiskPlan(
            entry=round(entry, 2),
            stop=round(stop, 2),
            target=round(entry, 2),
            risk_per_share=0.0,
            reward_per_share=0.0,
            rr=0.0,
            hold_min_days=hold_min,
            hold_max_days=hold_max,
            passes_gate=False,
            current_price=round(current_price, 2),
            trigger_kind=trig.kind,
            extended=trig.extended,
        )

    # Technical target: lower of (ATR projection) and (R:R floor).
    # ATR projection sets the credibility ceiling — how far the stock can
    # realistically move in the hold window given its daily range.
    # R:R floor is the conservative minimum for the setup type.
    # Taking min() means wide-stop setups where ATR can't support the
    # required R-multiple will produce a sub-gate R:R and be filtered out,
    # rather than showing a phantom target that the stock won't reach.
    atr_target = entry + ATR_TARGET_MULT.get(setup, r_mult) * atr14
    rr_floor_target = entry + r_mult * risk
    if atr_target <= rr_floor_target:
        target = atr_target
        target_method = "atr_projection"  # ATR caps target — stop too wide for r_mult at this ATR
    else:
        target = rr_floor_target
        target_method = "rr_floor"  # R:R floor is tighter — tight stop, conservative target
    rr = (target - entry) / risk
    return RiskPlan(
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        risk_per_share=round(risk, 2),
        reward_per_share=round(target - entry, 2),
        rr=round(rr, 2),
        hold_min_days=hold_min,
        hold_max_days=hold_max,
        passes_gate=rr >= RR_HARD_GATE,
        current_price=round(current_price, 2),
        trigger_kind=trig.kind,
        extended=trig.extended,
        target_method=target_method,
    )
