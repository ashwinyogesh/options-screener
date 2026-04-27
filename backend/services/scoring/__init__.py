"""
Scoring package — pure functions and constants that turn indicator values into
0–100 scores for the three screeners (CSP, CC, DITM).

Structure:
- `config.py`  — weight constants (ENV_WEIGHTS, STRIKE_WEIGHTS, …). Single source
                 of truth; do not duplicate elsewhere.
- `env.py`     — environment scorers (`compute_env_score`, `compute_ditm_env_score`).
- `strike.py`  — strike-quality scorers + final blend helpers.

These modules are deliberately I/O-free: no FastAPI, no yfinance, no DB. They
take primitives / dataclasses in and return primitives / dataclasses out, which
keeps them easy to unit-test and reuse from the upcoming `ScreenerService`.
"""
