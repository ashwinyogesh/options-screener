"""
Swing-trade calibrated probability scorer (v3.0 — Lasso).

Replaces the v2.3 additive bucket scorer (rr/setup/ctx/inst) with a
calibrated probability classifier:

    score = round(100 × P_calibrated(target_hit))

Pipeline at inference:
    1. Build the feature vector in the exact order the model was trained on
    2. Standardise (x - mean) / std
    3. Apply L1 logistic regression: p_raw = sigmoid(intercept + coef·x_scaled)
    4. Map through isotonic calibration → p_calibrated
    5. Score = round(p_calibrated * 100)

Model artefact: swing_lasso_model.json (this directory).
Training/evaluation: scripts/swing_lasso_scorer.py
Methodology: docs/SWING_METHODOLOGY.md (v3 section)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SWING_LASSO_VERSION: str = "3.0.0-lasso"
_MODEL_PATH: Path = Path(__file__).resolve().parent / "swing_lasso_model.json"


@dataclass(frozen=True, slots=True)
class _LoadedModel:
    features: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    iso_x: tuple[float, ...]
    iso_y: tuple[float, ...]
    brier_raw: float
    brier_calibrated: float
    base_win_rate: float

    @classmethod
    def load(cls, path: Path = _MODEL_PATH) -> "_LoadedModel":
        with path.open() as fh:
            d = json.load(fh)
        return cls(
            features=tuple(d["features"]),
            mean=tuple(float(x) for x in d["scaler_mean"]),
            std=tuple(float(x) if x != 0 else 1.0 for x in d["scaler_std"]),
            coef=tuple(float(x) for x in d["logreg_coef"]),
            intercept=float(d["logreg_intercept"]),
            iso_x=tuple(float(x) for x in d["isotonic_x"]),
            iso_y=tuple(float(x) for x in d["isotonic_y"]),
            brier_raw=float(d.get("brier_raw", 0.0)),
            brier_calibrated=float(d.get("brier_calibrated", 0.0)),
            base_win_rate=float(d.get("base_win_rate", 0.5)),
        )


_MODEL: _LoadedModel | None = None


def get_model() -> _LoadedModel:
    """Lazy-load the model on first call."""
    global _MODEL
    if _MODEL is None:
        _MODEL = _LoadedModel.load()
    return _MODEL


# --------------------------------------------------------------------- math


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _isotonic_predict(p: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """Piecewise-linear interpolation between isotonic knots (clipped)."""
    if not xs:
        return p
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    # binary search would be nicer; ~32 knots → linear is fine
    for i in range(1, len(xs)):
        if p <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if x1 == x0:
                return y1
            t = (p - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return ys[-1]


# ---------------------------------------------------------------- scoring


def compute_swing_score_lasso(
    feature_values: dict[str, float],
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Score one setup with the L1-logistic + isotonic-calibrated model.

    Args:
        feature_values: mapping feature_name → numeric value. Must include
            every feature in the trained model's feature list. Missing
            features default to 0 (post-standardisation that means "mean
            value") — but a warning is included in the output.
        top_k: how many of the highest-|contribution| features to surface
            in the breakdown for UI display.

    Returns:
        {
            "score": int 0-100 (calibrated probability × 100),
            "p_raw": float 0-1 (pre-calibration logistic output),
            "p_target": float 0-1 (calibrated probability of hitting target),
            "confidence": "high" | "medium" | "speculative",
            "top_features": [{name, value, std_value, contribution}, ...],
            "missing_features": [str, ...],
            "version": str,
        }
    """
    m = get_model()

    missing: list[str] = []
    z: list[float] = []
    for feat, mean, std in zip(m.features, m.mean, m.std, strict=True):
        v = feature_values.get(feat)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            missing.append(feat)
            z.append(0.0)
            continue
        z.append((float(v) - mean) / (std if std else 1.0))

    contributions = [zi * ci for zi, ci in zip(z, m.coef, strict=True)]
    logit = m.intercept + sum(contributions)
    p_raw = _sigmoid(logit)
    p_cal = _isotonic_predict(p_raw, m.iso_x, m.iso_y)
    p_cal = max(0.0, min(1.0, p_cal))
    score = int(round(p_cal * 100))

    # Top contributors (|β·z|) for UI breakdown
    indexed = sorted(
        enumerate(contributions), key=lambda t: abs(t[1]), reverse=True
    )[:top_k]
    top_features = [
        {
            "name": m.features[i],
            "value": float(feature_values.get(m.features[i], 0.0) or 0.0),
            "std_value": round(z[i], 3),
            "coef": round(m.coef[i], 4),
            "contribution": round(contributions[i], 4),
        }
        for i, _ in indexed
        if m.coef[i] != 0.0
    ]

    if p_cal >= 0.65:
        confidence = "high"
    elif p_cal >= 0.50:
        confidence = "medium"
    else:
        confidence = "speculative"

    return {
        "score": score,
        "p_raw": round(p_raw, 4),
        "p_target": round(p_cal, 4),
        "confidence": confidence,
        "top_features": top_features,
        "missing_features": missing,
        "version": SWING_LASSO_VERSION,
    }


__all__ = [
    "SWING_LASSO_VERSION",
    "compute_swing_score_lasso",
    "get_model",
]
