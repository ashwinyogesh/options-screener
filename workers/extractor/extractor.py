"""OpenAI-based ticker and sentiment extraction (Layer 3 per NARRATIVE_METHODOLOGY §3).

Extraction prompt asks GPT-4o-mini to return a JSON array of signals.
Each signal has: ticker, sentiment (bullish/bearish), confidence (0-1), and a
one-sentence rationale grounded in the post text (specific catalyst/number/
event — no generic summaries).

Cost gate (Layer 1): posts with body length < 20 chars skip OpenAI and are
discarded. Score-based filtering is deferred to Phase 3 aggregation — Arctic
Shift returns score=1 for posts < 36h old (archival lag), making score an
unreliable real-time gate. See NARRATIVE_METHODOLOGY.md §3 and ADR-0016.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import openai
from openai import AzureOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a financial signal extractor. Given a Reddit post or comment, extract \
every stock ticker mentioned with a clear bullish or bearish opinion expressed \
by the author. Return a JSON object with a single key "signals" whose value is \
an array — no markdown, no prose.

Each element must have exactly these fields:
  "ticker"     : string — uppercase US stock ticker (e.g. "NVDA")
  "sentiment"  : one of "bullish", "bearish"
  "confidence" : float 0.0-1.0 (how clearly the opinion is stated)
  "rationale"  : string — one concise sentence naming the specific catalyst, \
thesis, or evidence: include exact numbers, product names, or events if \
mentioned (e.g. "Q2 EPS beat by 18%, data-center revenue +42% YoY driving \
multiple expansion" or "FDA rejection of lead drug, pipeline now empty"). \
Do NOT write generic summaries like "author is bullish on X".

Rules:
- Omit tickers where opinion is absent or ambiguous (confidence < 0.3).
- Omit crypto, ETFs, and non-US tickers.
- Do NOT extract "neutral" sentiment — only bullish or bearish.
- Return {"signals": []} if no clear directional signals exist.
- Never invent tickers not present in the text.
- The rationale must be grounded in the post text; do not add external facts."""

# Layer 1 cost gate — skip posts too short to contain meaningful signal.
# RSS posts are often link submissions; body = title only (~30-60 chars).
_MIN_BODY_LEN = 20

# Sentiment whitelist (ADR-0022): only directional signals reach the
# classifier. Neutrals are ambiguous, inflate cost without informing
# Component D, and produce noise in axis ratios.
_ALLOWED_SENTIMENTS: frozenset[str] = frozenset({"bullish", "bearish"})


@dataclass
class ExtractedSignal:
    ticker: str
    sentiment: str
    confidence: float
    rationale: str
    post_id: str
    subreddit: str
    author_hash: str
    created_utc: int
    source: str
    # Reddit post flair (e.g. "DD", "News", "Discussion"). Forwarded from the
    # ingestion event verbatim so aggregator dd_post_ratio can fire on real data.
    flair: str | None = None


# Minimum seconds between OpenAI calls. 1.25s ≈ 48 RPM, safely under the
# 50 RPM Azure OpenAI quota for gpt-4o-mini. Configurable via constructor
# so tests can pass 0 to disable throttling.
_DEFAULT_MIN_CALL_INTERVAL: float = 1.25


class Extractor:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        max_tokens: int = 800,
        min_call_interval: float = _DEFAULT_MIN_CALL_INTERVAL,
    ) -> None:
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version="2024-08-01-preview",
        )
        self._deployment = deployment
        self._max_tokens = max_tokens
        self._min_call_interval = min_call_interval
        # Monotonic timestamp of the last completed OpenAI call. Initialized
        # to -inf so the first call is never throttled.
        self._last_call_at: float = -1e9

    def extract(self, event: dict) -> list[ExtractedSignal]:
        """Run Layer 1 gate then call OpenAI. Returns [] if gated or no signals."""
        body: str = event.get("body", "")

        # Gate: skip posts too short to contain meaningful signal.
        if len(body) < _MIN_BODY_LEN:
            logger.debug("Gated post %s: body_len=%d", event.get('post_id'), len(body))
            return []

        raw = self._call_openai(body[:4000])  # cap prompt size
        signals = []
        for item in raw:
            try:
                sentiment = str(item["sentiment"]).strip().lower()
                # Enum gate (ADR-0022): only directional signals are admitted.
                # The prompt forbids "neutral" but `response_format` is
                # `json_object` (free-form), so the model occasionally still
                # emits it. Drop anything outside the whitelist rather than
                # leak ambiguity into the classifier.
                if sentiment not in _ALLOWED_SENTIMENTS:
                    logger.debug(
                        "Dropped non-directional signal sentiment=%r ticker=%s",
                        sentiment, item.get("ticker"),
                    )
                    continue
                signals.append(ExtractedSignal(
                    ticker=str(item["ticker"]).upper(),
                    sentiment=sentiment,
                    confidence=float(item["confidence"]),
                    rationale=str(item["rationale"]),
                    post_id=event.get("post_id", ""),
                    subreddit=event.get("subreddit", ""),
                    author_hash=event.get("author_hash", ""),
                    created_utc=int(event.get("created_utc", 0)),
                    source=event.get("source", "reddit_json"),
                    flair=event.get("flair"),
                ))
            except (KeyError, ValueError, TypeError):
                logger.warning("Malformed signal item from OpenAI: %s", item)
        return signals

    @retry(
        # Only retry transient OpenAI errors. Catching bare Exception here
        # would mask programming bugs and make the job appear to succeed when
        # it silently swallowed a TypeError or KeyError.
        retry=retry_if_exception_type((
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
        )),
        # 429 Retry-After is typically 30-60s on Azure OpenAI; start at 15s.
        wait=wait_exponential(multiplier=2, min=15, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_openai(self, body: str) -> list[dict]:
        # Throttle: enforce minimum interval between successive API calls to
        # stay under the 50 RPM Azure OpenAI quota. Applied before every
        # attempt (including retries) so backpressure accumulates correctly.
        now = time.monotonic()
        wait = self._min_call_interval - (now - self._last_call_at)
        if wait > 0:
            time.sleep(wait)
        self._last_call_at = time.monotonic()

        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": body},
            ],
            max_tokens=self._max_tokens,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        # Prompt requests {"signals": [...]}; unwrap the key.
        # Bare-list fallback retained for defensive robustness.
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "signals" in parsed and isinstance(parsed["signals"], list):
            return parsed["signals"]
        if isinstance(parsed, list):
            return parsed
        # Try other common wrapper keys as a last resort.
        for key in ("results", "data", "tickers"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        logger.warning("Unexpected OpenAI response shape: %s", list(parsed.keys()))
        return []
