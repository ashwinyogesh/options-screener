"""
Precision evaluation for the extractor (Phase 2 test milestone).

Tests that GPT-4o-mini extraction achieves:
  - precision ≥ 0.92 at high confidence   (confidence ≥ 0.70)
  - precision ≥ 0.80 at medium confidence (confidence ≥ 0.40)

Per NARRATIVE_METHODOLOGY.md §8 Phase 2 milestone.

IMPORTANT: This test uses pre-captured extractor outputs stored in
  backend/tests/fixtures/extractor/labeled_mentions.jsonl

It makes NO live OpenAI calls. To populate captured_output, run:
  cd backend
  .\\venv\\Scripts\\python.exe ..\\scripts\\capture_extractor_fixtures.py

The test is skipped (not failed) if fewer than MIN_CAPTURED_ENTRIES entries
have been captured, with a clear message directing to the capture script.

Precision definition used here:
  A predicted (ticker, sentiment) pair is a True Positive if:
    - The ticker matches a human label for that entry (case-insensitive)
    - The sentiment matches the human label for that ticker
  A predicted ticker not in human labels = False Positive.
  A predicted ticker in labels but with wrong sentiment = False Positive.
  Entries with human_labels=[] are "no-signal" examples; any predicted signal
  is a False Positive.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "extractor"
    / "labeled_mentions.jsonl"
)

# Minimum number of captured entries before the precision assertion fires.
# Below this, we skip rather than fail — the dataset is still being built.
_MIN_CAPTURED_ENTRIES = 20

# Precision thresholds (§8 Phase 2 milestone)
_PRECISION_HIGH_CONF_THRESHOLD = 0.92   # confidence ≥ 0.70
_PRECISION_MED_CONF_THRESHOLD = 0.80    # confidence ≥ 0.40
_HIGH_CONF_CUTOFF = 0.70
_MED_CONF_CUTOFF = 0.40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_captured_entries() -> list[dict]:
    """Return fixture entries that have been captured (captured_output != null)."""
    if not _FIXTURE_PATH.exists():
        return []
    entries = []
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("captured_output") is not None:
                entries.append(entry)
    return entries


def _build_label_map(human_labels: list[dict]) -> dict[str, str]:
    """Return {ticker_upper: sentiment} from human labels."""
    return {lbl["ticker"].upper(): lbl["sentiment"] for lbl in human_labels}


def _compute_precision(
    entries: list[dict],
    min_confidence: float,
) -> tuple[float, int, int]:
    """Return (precision, tp_count, fp_count) over all entries.

    Only predictions with confidence ≥ min_confidence are evaluated.
    Entries with no qualifying predictions are skipped (neither TP nor FP).
    """
    tp = 0
    fp = 0
    for entry in entries:
        label_map = _build_label_map(entry.get("human_labels", []))
        predictions = entry.get("captured_output", []) or []
        for pred in predictions:
            conf = float(pred.get("confidence", 0.0))
            if conf < min_confidence:
                continue
            ticker = pred.get("ticker", "").upper()
            sentiment = pred.get("sentiment", "")
            if ticker in label_map and label_map[ticker] == sentiment:
                tp += 1
            else:
                fp += 1
    total = tp + fp
    precision = tp / total if total > 0 else 1.0  # no predictions = trivially precise
    return precision, tp, fp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def captured_entries() -> list[dict]:
    return _load_captured_entries()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fixture_file_exists() -> None:
    """Fixture file must be present (even if empty of captured outputs)."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture file missing: {_FIXTURE_PATH}\n"
        "This file should be committed — it contains hand-labeled test cases."
    )


def test_fixture_has_seed_entries() -> None:
    """There should be at least 20 seed entries in the fixture."""
    all_entries = []
    if _FIXTURE_PATH.exists():
        with _FIXTURE_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    all_entries.append(json.loads(line))
    assert len(all_entries) >= 20, (
        f"Expected ≥20 seed entries in fixture, found {len(all_entries)}.\n"
        "Add more labeled examples to labeled_mentions.jsonl."
    )


def test_precision_high_confidence(captured_entries: list[dict]) -> None:
    """Precision ≥ 0.92 at confidence ≥ 0.70 (Phase 2 milestone, high tier)."""
    if len(captured_entries) < _MIN_CAPTURED_ENTRIES:
        pytest.skip(
            f"Only {len(captured_entries)} captured entries (need {_MIN_CAPTURED_ENTRIES}). "
            "Run: cd backend && .\\venv\\Scripts\\python.exe ..\\scripts\\capture_extractor_fixtures.py"
        )
    precision, tp, fp = _compute_precision(captured_entries, _HIGH_CONF_CUTOFF)
    assert precision >= _PRECISION_HIGH_CONF_THRESHOLD, (
        f"High-confidence precision {precision:.3f} < {_PRECISION_HIGH_CONF_THRESHOLD} "
        f"(tp={tp}, fp={fp}, entries={len(captured_entries)})\n"
        "Precision = TP/(TP+FP) where TP = (ticker, sentiment) matches human label."
    )


def test_precision_medium_confidence(captured_entries: list[dict]) -> None:
    """Precision ≥ 0.80 at confidence ≥ 0.40 (Phase 2 milestone, medium tier)."""
    if len(captured_entries) < _MIN_CAPTURED_ENTRIES:
        pytest.skip(
            f"Only {len(captured_entries)} captured entries (need {_MIN_CAPTURED_ENTRIES}). "
            "Run: cd backend && .\\venv\\Scripts\\python.exe ..\\scripts\\capture_extractor_fixtures.py"
        )
    precision, tp, fp = _compute_precision(captured_entries, _MED_CONF_CUTOFF)
    assert precision >= _PRECISION_MED_CONF_THRESHOLD, (
        f"Medium-confidence precision {precision:.3f} < {_PRECISION_MED_CONF_THRESHOLD} "
        f"(tp={tp}, fp={fp}, entries={len(captured_entries)})\n"
        "Medium tier includes lower-confidence predictions — more noise expected."
    )


def test_no_signal_false_positive_rate(captured_entries: list[dict]) -> None:
    """No-signal posts (human_labels=[]) should produce few high-confidence predictions.

    FP rate > 20% on no-signal posts suggests the extractor is hallucinating tickers.
    """
    no_signal = [e for e in captured_entries if not e.get("human_labels")]
    if not no_signal:
        pytest.skip("No no-signal entries captured yet.")

    fp_count = 0
    for entry in no_signal:
        for pred in (entry.get("captured_output") or []):
            if float(pred.get("confidence", 0.0)) >= _HIGH_CONF_CUTOFF:
                fp_count += 1

    fp_rate = fp_count / len(no_signal)
    assert fp_rate <= 0.20, (
        f"No-signal FP rate {fp_rate:.2f} > 0.20 "
        f"({fp_count} high-confidence predictions across {len(no_signal)} no-signal posts). "
        "Extractor may be hallucinating tickers. Check prompt or lower confidence threshold."
    )


def test_precision_report(captured_entries: list[dict], capsys) -> None:
    """Print a human-readable precision report. Always passes — informational only."""
    if not captured_entries:
        print("\nNo captured entries yet. Run capture_extractor_fixtures.py.")
        return

    p_high, tp_h, fp_h = _compute_precision(captured_entries, _HIGH_CONF_CUTOFF)
    p_med, tp_m, fp_m = _compute_precision(captured_entries, _MED_CONF_CUTOFF)
    no_signal_entries = [e for e in captured_entries if not e.get("human_labels")]
    has_signal_entries = [e for e in captured_entries if e.get("human_labels")]

    print(f"""
=== Extractor Precision Report ===
Captured entries  : {len(captured_entries)}
  With signal     : {len(has_signal_entries)}
  No-signal       : {len(no_signal_entries)}

High confidence (≥{_HIGH_CONF_CUTOFF:.2f}):
  Precision       : {p_high:.3f}  (target ≥ {_PRECISION_HIGH_CONF_THRESHOLD})
  TP={tp_h}  FP={fp_h}  Total predictions={tp_h + fp_h}

Medium confidence (≥{_MED_CONF_CUTOFF:.2f}):
  Precision       : {p_med:.3f}  (target ≥ {_PRECISION_MED_CONF_THRESHOLD})
  TP={tp_m}  FP={fp_m}  Total predictions={tp_m + fp_m}

Path to 500 entries: add more labeled_mentions.jsonl rows (source="arctic_shift")
then re-run scripts/capture_extractor_fixtures.py.
==================================
""")
