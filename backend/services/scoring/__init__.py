"""
Scoring package — pure functions and constants that turn indicator values into
0–100 scores for the CSP, CC, and DITM screeners.

Structure:
- `config.py`  — weight constants.
- `env.py`     — `compute_env_score` (CSP/CC, direction-aware).
- `strike.py`  — CSP/CC strike-quality scorers + final-blend helpers.
- `ditm.py`    — `compute_ditm_env_score` + `compute_ditm_strike_score`.

All modules are I/O-free: no FastAPI, no yfinance, no DB.
"""
