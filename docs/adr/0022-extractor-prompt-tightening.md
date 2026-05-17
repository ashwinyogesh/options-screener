# ADR-0022 — Extractor prompt tightening and directional-only signals

**Status:** Accepted
**Date:** 2026-05-16
**Amends:** [ADR-0015](0015-extractor-architecture-simplification.md),
[ADR-0016](0016-extractor-runtime-defaults.md)

## Context

The extractor (Layer 3 in [NARRATIVE_METHODOLOGY](../NARRATIVE_METHODOLOGY.md))
calls GPT-4o-mini per Reddit post to produce a JSON array of
`(ticker, sentiment, confidence, rationale)` tuples. The original schema
admitted three sentiment values: `bullish`, `bearish`, `neutral`.

Three problems with `neutral`:

1. **No downstream consumer.** Component D, lifecycle staging, and the
   axis-based conviction classifier (ADR-0020 / ADR-0021) all reason about
   *directional* opinion. Neutral signals dilute mention counts and add
   zero information to the score.
2. **Cost without yield.** Every neutral signal still pays for an axis
   classifier call (`job-classifier`) and an embedding call. Inspection of
   `backend/tests/fixtures/extractor/labeled_mentions.jsonl` shows ~30% of
   captured outputs were neutral pass-throughs that human labelers either
   left unlabeled or labeled differently — i.e. the model used `neutral`
   as a hedge for "ticker mentioned, opinion unclear" rather than as a
   precise signal.
3. **Rationale drift.** Neutral rationales tend toward generic restatements
   ("X is mentioned in the post") because there is no opinion to ground
   them, encouraging the same loose pattern in bullish/bearish outputs.

A second issue, independent of `neutral` but shipped together: rationales
were too generic to be useful for the axis classifier's downstream
prompt. The classifier infers `substance ∈ {researched, emotional}` from
rationale text, and generic summaries ("author is bullish on X") trivially
classify as `emotional` regardless of the underlying post quality.

## Decision

Three coordinated changes to `workers/extractor/extractor.py`:

1. **Drop `neutral` from the sentiment schema.** Prompt now states
   `"sentiment": one of "bullish", "bearish"` and explicitly forbids
   `neutral` in the rules block.
2. **Belt-and-suspenders post-parse filter.** `response_format` remains
   `{"type": "json_object"}` (free-form JSON), so the model may still emit
   `neutral` occasionally. A whitelist `_ALLOWED_SENTIMENTS = {"bullish",
   "bearish"}` in `extractor.py` drops any non-conforming row at parse
   time. Logged at DEBUG, not WARNING — the model occasionally hedging is
   expected, not an error.
3. **Rationale grounding requirement.** Prompt now requires the rationale
   to name a specific catalyst, number, product, or event, with positive
   and negative examples. `max_tokens` bumped from 512 to 800 to fit
   richer rationales without truncation.

A future upgrade to OpenAI Structured Outputs
(`response_format={"type": "json_schema", ...}` with `enum` on `sentiment`)
would make the filter unnecessary, but requires bumping the Azure
OpenAI API version pin and is out of scope here.

## Consequences

**Expected**
- Signal volume drops 25–35% (the historical `neutral` rate). Mention
  counts in `ticker_timeline` will fall proportionally; Component A/B
  thresholds may need a recalibration sweep but are unlikely to drift far
  since they were already tuned against the directional subset.
- OpenAI cost on the classifier and embedder drops by roughly the same
  fraction.
- Component D becomes more responsive: the denominator
  (`conviction_classified_14d`) stops being inflated by ambiguous posts.
- Rationale quality improvements indirectly improve axis classification
  precision (the classifier sees concrete evidence rather than generic
  summaries).

**Risks**
- Existing fixtures in `backend/tests/fixtures/extractor/labeled_mentions.jsonl`
  contain `neutral` captured_output rows that will no longer be produced
  by the new prompt. The pre-existing failures in
  `tests/integration/test_extractor_precision.py` are unrelated to this
  change but will need fixture recapture
  (`scripts/capture_extractor_fixtures.py`) before that suite can be
  re-baselined.
- A small fraction of weakly-bullish posts that previously fell into
  `neutral` will now either be dropped entirely (the model picks neutral,
  filter drops it) or shift to `bullish/bearish` with lower confidence.
  Both outcomes are acceptable — the confidence ≥ 0.3 gate downstream
  already filters low-conviction extractions.

**Reversibility**
- High. Re-add `neutral` to the prompt and to `_ALLOWED_SENTIMENTS` in
  one file. No schema migration needed since `sentiment` is a free-form
  string on the Cosmos `signals` document.

## Operational notes

- No backfill of historical `signals` documents. Existing `neutral` rows
  remain on disk until the 90-day TTL expires; nothing reads them after
  this change.
- Aggregator/classifier defaults that still substitute `"neutral"` when
  the field is missing
  (`workers/aggregator/cosmos_reader.py`,
   `workers/classifier/main.py`,
   `backend/services/narrative/attention.py`)
  are left in place — they protect against malformed legacy documents,
  not against new extractor output.

## References

- ADR-0015: extractor architecture simplification
- ADR-0016: extractor runtime defaults
- ADR-0020: multi-axis conviction schema (rationale quality matters
  more once substance is a first-class axis)
- ADR-0021: retire legacy conviction taxonomy
