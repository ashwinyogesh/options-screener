"""
Capture extractor outputs for precision evaluation fixtures.

Run from repo root ONCE (or when re-calibrating after a model change):

    cd backend
    .\venv\Scripts\python.exe ..\scripts\capture_extractor_fixtures.py

What it does:
- Reads backend/tests/fixtures/extractor/labeled_mentions.jsonl
- For each entry where captured_output is null, calls Extractor.extract() live
  against Azure OpenAI (requires KEYVAULT_URI env var or local .env with
  AZURE_OPENAI_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT set)
- Writes the extractor's output back into captured_output in the JSONL
- Saves the updated JSONL in place

Why this exists:
- The precision test (test_extractor_precision.py) uses stored captured_output
  to compute precision metrics without making live OpenAI calls in CI.
- This script is the one-time capture step. Re-run it intentionally when:
  (a) the extractor prompt changes, (b) the model is upgraded, or (c) new
  labeled entries are added to the JSONL.

Do NOT run this in CI. It costs money and produces non-deterministic output.

Cost estimate: ~30 entries × ~200 tokens each ≈ 6,000 tokens ≈ $0.001 (GPT-4o-mini).
For 500 entries: ~100,000 tokens ≈ $0.015.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

# Make workers/extractor importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXTRACTOR_ROOT = _REPO_ROOT / "workers" / "extractor"
if str(_EXTRACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTRACTOR_ROOT))

# Make backend importable (for dotenv loading)
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

FIXTURE_PATH = _REPO_ROOT / "backend" / "tests" / "fixtures" / "extractor" / "labeled_mentions.jsonl"


def _load_credentials() -> tuple[str, str, str]:
    """Return (api_key, endpoint, deployment) from env or .env file."""
    # Try dotenv first (local dev)
    try:
        from dotenv import load_dotenv
        load_dotenv(_BACKEND_ROOT / ".env")
    except ImportError:
        pass

    # Try direct env vars first (CI / local)
    api_key = os.getenv("AZURE_OPENAI_KEY") or os.getenv("OPENAI_API_KEY", "")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    if api_key and endpoint:
        logger.info("Using direct env vars for OpenAI credentials")
        return api_key, endpoint, deployment

    # Fall back to Key Vault if KEYVAULT_URI is set
    keyvault_uri = os.getenv("KEYVAULT_URI", "")
    if keyvault_uri:
        logger.info("Fetching credentials from Key Vault: %s", keyvault_uri)
        from kv_secrets import fetch_secrets
        secrets = fetch_secrets(keyvault_uri)
        return secrets.openai_api_key, secrets.openai_endpoint, secrets.openai_deployment

    raise RuntimeError(
        "No OpenAI credentials found. Set AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT "
        "or KEYVAULT_URI env vars."
    )


def main() -> None:
    if not FIXTURE_PATH.exists():
        logger.error("Fixture file not found: %s", FIXTURE_PATH)
        sys.exit(1)

    entries = []
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    needs_capture = [e for e in entries if e.get("captured_output") is None]
    logger.info(
        "%d total entries, %d need capture, %d already captured",
        len(entries), len(needs_capture), len(entries) - len(needs_capture),
    )

    if not needs_capture:
        logger.info("All entries already captured. Nothing to do.")
        return

    api_key, endpoint, deployment = _load_credentials()

    from extractor import Extractor
    extractor = Extractor(
        api_key=api_key,
        endpoint=endpoint,
        deployment=deployment,
        max_tokens=512,
    )

    captured = 0
    errors = 0
    for entry in needs_capture:
        entry_id = entry.get("id", "?")
        body = entry.get("body", "")
        if not body:
            entry["captured_output"] = []
            continue
        try:
            # Build a minimal event dict matching the extractor's expected shape
            event = {
                "body": body,
                "post_id": entry_id,
                "subreddit": "seed",
                "author_hash": "seed",
                "created_utc": 0,
                "source": "seed",
            }
            signals = extractor.extract(event)
            entry["captured_output"] = [
                {
                    "ticker": s.ticker,
                    "sentiment": s.sentiment,
                    "confidence": round(s.confidence, 3),
                    "rationale": s.rationale,
                }
                for s in signals
            ]
            logger.info(
                "  %s → %d signals: %s",
                entry_id,
                len(signals),
                [(s.ticker, s.sentiment) for s in signals],
            )
            captured += 1
        except Exception:
            logger.exception("Failed to capture entry %s", entry_id)
            errors += 1

    # Write updated JSONL back in place
    with FIXTURE_PATH.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    logger.info(
        "Done. captured=%d errors=%d total_with_output=%d",
        captured,
        errors,
        sum(1 for e in entries if e.get("captured_output") is not None),
    )


if __name__ == "__main__":
    main()
