"""Market-implied (IV) prior + Bayesian posterior for ETV scenario probabilities.

Implements Option 1 + Option 4 of the ETV asymmetry-hardening design:

* **Option 1 — IV prior**: under a lognormal cone with annualised IV and a
  drift of ``-σ²T/2`` (drift-free / risk-neutral), turn three scenario
  prices (bear, base, bull) into a calibrated prior over bear/base/bull
  buckets.  Bucket cutoffs are the midpoints between adjacent scenario
  prices, so the prior is uniquely determined by the LLM's own prices.

* **Option 4 — Likelihood-ratio update**: the LLM no longer emits a raw
  probability per scenario.  It emits a ``likelihood_ratio`` in
  ``[LR_MIN, LR_MAX]`` expressing how much MORE (LR > 1) or LESS (LR < 1)
  likely that scenario is relative to the market's lognormal cone.  The
  server clamps the LRs, multiplies element-wise into the prior, and
  renormalises::

      posterior_s = clamp(LR_s) · prior_s / Σ_k clamp(LR_k) · prior_k

  The clamp caps how much the LLM can move the gate (about ±1.4σ of
  disagreement at the extremes).

This module is intentionally tiny and dependency-free (uses ``math``
only) so it is trivial to unit-test and to call from the validator.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- consts

#: Annualised-IV horizon-day mapping used by the orchestrator.  Mirrors
#: the ``Horizon`` literal in ``llm.py`` (short/medium/long).
HORIZON_DAYS: dict[str, int] = {"short": 30, "medium": 90, "long": 180}

#: Bounds on the LLM's likelihood ratio.  An LR of 4.0 (or 0.25) means
#: the LLM believes the scenario is 4x more (or 1/4x as) likely as the
#: market's lognormal cone suggests.  Anything beyond that is silently
#: clamped — large disagreement should be expressed via a different
#: scenario price, not an extreme weight.
LR_MIN: float = 0.25
LR_MAX: float = 4.0


# ------------------------------------------------------------- helpers

def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via ``math.erf``."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def horizon_to_days(horizon: str | None) -> int | None:
    """Map a ``Horizon`` literal to integer days.  Returns ``None`` for
    unknown / missing values so the caller can fall back gracefully."""
    if not horizon or not isinstance(horizon, str):
        return None
    return HORIZON_DAYS.get(horizon.lower())


# --------------------------------------------------------- IV prior

def lognormal_scenario_probs(
    spot: float,
    iv_annual: float,
    horizon_days: int,
    bear: float,
    base: float,
    bull: float,
) -> tuple[float, float, float]:
    """Return ``(p_bear, p_base, p_bull)`` under the lognormal cone.

    The price of the underlying at time ``T`` is modelled as
    ``S_T = spot · exp(μ T + σ √T · Z)`` with ``Z ~ N(0, 1)``, drift
    ``μ T = -σ² T / 2`` (martingale measure, no carry/yield assumed),
    and ``σ = iv_annual``.

    Bucket cutoffs are placed at the midpoints between adjacent scenario
    prices so the result is invariant to monotonic relabelling of the
    scenarios and uses only the LLM's own grounded prices.

    Returned probabilities sum to exactly 1.0 (the last bucket is
    ``1 − Φ(z₂)``) and each component is in ``[0, 1]``.

    Raises ``ValueError`` on non-positive ``spot`` / ``iv_annual`` /
    ``horizon_days``.  The caller is expected to guard against missing
    inputs upstream.
    """
    if spot <= 0:
        raise ValueError(f"spot must be > 0, got {spot}")
    if iv_annual <= 0:
        raise ValueError(f"iv_annual must be > 0, got {iv_annual}")
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be > 0, got {horizon_days}")

    sigma_T = iv_annual * math.sqrt(horizon_days / 365.0)
    mu_T = -0.5 * sigma_T * sigma_T

    # Sort prices to make cutoffs well-defined even if the LLM emits
    # scenarios out of order (rare but possible — e.g. a degenerate base
    # case).  We always treat the lowest as "bear bucket", middle as
    # "base bucket", highest as "bull bucket" and then re-label the
    # returned tuple to match the caller's (bear, base, bull) prices.
    items = [("bear", bear), ("base", base), ("bull", bull)]
    sorted_items = sorted(items, key=lambda x: x[1])
    p_lo = sorted_items[0][1]
    p_mid = sorted_items[1][1]
    p_hi = sorted_items[2][1]

    if p_lo <= 0 or p_mid <= 0 or p_hi <= 0:
        raise ValueError(
            f"scenario prices must all be > 0, got "
            f"bear={bear} base={base} bull={bull}"
        )

    c1 = (p_lo + p_mid) / 2.0
    c2 = (p_mid + p_hi) / 2.0

    z1 = (math.log(c1 / spot) - mu_T) / sigma_T
    z2 = (math.log(c2 / spot) - mu_T) / sigma_T

    p_low = _norm_cdf(z1)
    p_mid_bucket = _norm_cdf(z2) - p_low
    p_high = 1.0 - _norm_cdf(z2)

    # Numerical safety: clamp tiny negatives produced by float subtraction
    # in the middle bucket, and renormalise.
    p_low = max(0.0, p_low)
    p_mid_bucket = max(0.0, p_mid_bucket)
    p_high = max(0.0, p_high)
    z = p_low + p_mid_bucket + p_high
    if z <= 0:
        # Pathological case — fall back to flat prior so the caller can
        # still proceed.
        return (1 / 3, 1 / 3, 1 / 3)
    p_low, p_mid_bucket, p_high = p_low / z, p_mid_bucket / z, p_high / z

    # Re-label the buckets back to (bear, base, bull) following the
    # caller's order.  Build a dict keyed by the original label.
    by_label = {
        sorted_items[0][0]: p_low,
        sorted_items[1][0]: p_mid_bucket,
        sorted_items[2][0]: p_high,
    }
    return (by_label["bear"], by_label["base"], by_label["bull"])


# ------------------------------------------------------ Bayesian update

def apply_likelihood_ratios(
    prior: tuple[float, float, float],
    lrs: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Bayesian posterior from a 3-bucket prior and 3 likelihood ratios.

    Returns ``(posterior, clamped_lrs)`` where each LR has been clamped
    to ``[LR_MIN, LR_MAX]`` and the posterior is renormalised to sum to
    1.0.  If the prior or all clamped LRs collapse to zero, returns the
    flat prior unchanged so the caller can detect the degenerate case.
    """
    clamped = tuple(max(LR_MIN, min(LR_MAX, float(lr))) for lr in lrs)
    unnorm = tuple(c * p for c, p in zip(clamped, prior))
    z = sum(unnorm)
    if z <= 0:
        return (prior, clamped)
    posterior = tuple(u / z for u in unnorm)
    return (posterior, clamped)


# ---------------------------------------------------------- top-level

def compute_posterior(
    *,
    spot: float | None,
    iv_annual: float | None,
    horizon_days: int | None,
    scenarios: dict,
) -> dict | None:
    """One-shot helper used by the validator.

    ``scenarios`` is the report's ``economic_value`` (or ``etv``) block;
    we read ``{bear,base,bull}.price`` for the prior and
    ``{bear,base,bull}.likelihood_ratio`` for the LR update.  Returns a
    diagnostic dict ready to drop into ``report['validation']
    ['probability_check']``, or ``None`` if any required input is
    missing (the validator then falls back to the LLM's raw
    ``probability_pct``).

    The returned dict has the shape::

        {
          "prior":           {"bear": float, "base": float, "bull": float},
          "lr_llm":          {"bear": float, "base": float, "bull": float},
          "lr_clamped":      {"bear": float, "base": float, "bull": float},
          "posterior":       {"bear": float, "base": float, "bull": float},
          "applied":         True,
          "horizon_days":    int,
          "iv_annual":       float,
        }

    All probabilities are 0..1 floats (NOT percentages) — the validator
    multiplies by 100 when writing to scenario ``probability_pct``.
    """
    if spot is None or iv_annual is None or horizon_days is None:
        return None
    if spot <= 0 or iv_annual <= 0 or horizon_days <= 0:
        return None

    try:
        bear_px = float(scenarios["bear"]["price"])
        base_px = float(scenarios["base"]["price"])
        bull_px = float(scenarios["bull"]["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if any(p <= 0 for p in (bear_px, base_px, bull_px)):
        return None

    # All three scenarios MUST provide a numeric likelihood_ratio.  If
    # any is missing, fall back so we do not silently overwrite the
    # LLM's probability_pct with a flat-LR posterior (which would equal
    # the IV prior — a different feature; see Option 1 fallback below).
    try:
        lr_bear = float(scenarios["bear"]["likelihood_ratio"])
        lr_base = float(scenarios["base"]["likelihood_ratio"])
        lr_bull = float(scenarios["bull"]["likelihood_ratio"])
    except (KeyError, TypeError, ValueError):
        # Soft fallback: use LR=1 (Option 1 = pure IV prior, no LLM view).
        lr_bear = lr_base = lr_bull = 1.0
        lr_provided = False
    else:
        lr_provided = True

    prior = lognormal_scenario_probs(
        spot=float(spot),
        iv_annual=float(iv_annual),
        horizon_days=int(horizon_days),
        bear=bear_px,
        base=base_px,
        bull=bull_px,
    )

    posterior, clamped = apply_likelihood_ratios(prior, (lr_bear, lr_base, lr_bull))

    return {
        "prior": {"bear": prior[0], "base": prior[1], "bull": prior[2]},
        "lr_llm": {"bear": lr_bear, "base": lr_base, "bull": lr_bull},
        "lr_clamped": {"bear": clamped[0], "base": clamped[1], "bull": clamped[2]},
        "posterior": {
            "bear": posterior[0], "base": posterior[1], "bull": posterior[2],
        },
        "applied": True,
        "lr_provided": lr_provided,
        "horizon_days": int(horizon_days),
        "iv_annual": float(iv_annual),
    }
