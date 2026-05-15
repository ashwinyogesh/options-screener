"""OpenAI-based ticker and sentiment extraction (Layer 3 per NARRATIVE_METHODOLOGY §3).

Extraction prompt asks GPT-4o-mini to return a JSON array of signals.
Each signal has: ticker, sentiment (bullish/bearish/neutral), confidence (0-1),
and a one-sentence rationale.

Cost gate (Layer 1): posts with body length < 20 chars skip OpenAI and are
discarded. Score-based filtering is deferred to Phase 3 aggregation — Arctic
Shift returns score=1 for posts < 36h old (archival lag), making score an
unreliable real-time gate. See NARRATIVE_METHODOLOGY.md §3 and ADR-0016.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import AzureOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a financial signal extractor. Given a Reddit post or comment, extract
every stock ticker mentioned with a clear bullish or bearish opinion expressed
by the author. Return a JSON object with a single key "signals" whose value is
an array — no markdown, no prose.

Each element must have exactly these fields:
  "ticker"     : string — uppercase US stock ticker (e.g. "NVDA")
  "sentiment"  : one of "bullish", "bearish", "neutral"
  "confidence" : float 0.0-1.0 (how clearly the opinion is stated)
  "rationale"  : string — one sentence quoting or paraphrasing the key signal

Rules:
- Omit tickers where opinion is absent or ambiguous (confidence < 0.3).
- Omit crypto, ETFs, and non-US tickers.
- Return {"signals": []} if no clear signals exist.
- Never invent tickers not present in the text."""

# Layer 1 cost gate — skip posts too short to contain meaningful signal.
# RSS posts are often link submissions; body = title only (~30-60 chars).
_MIN_BODY_LEN = 20


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


class Extractor:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        max_tokens: int = 512,
    ) -> None:
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version="2024-08-01-preview",
        )
        self._deployment = deployment
        self._max_tokens = max_tokens

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
                signals.append(ExtractedSignal(
                    ticker=str(item["ticker"]).upper(),
                    sentiment=str(item["sentiment"]),
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
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_openai(self, body: str) -> list[dict]:
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
