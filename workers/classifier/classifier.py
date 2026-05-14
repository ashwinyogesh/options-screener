"""GPT-4o-mini conviction-state classifier (§3 of NARRATIVE_METHODOLOGY.md).

Pure function: takes a signal's ticker, sentiment, and rationale; returns
one of the 10 conviction states plus a confidence score.

Structured output via OpenAI JSON schema response_format — no parsing heuristics.

Prompt injection defence: instructions live in the `system` message; the
untrusted Reddit post body is sent as a separate `user` message. The model's
alignment ensures system instructions take precedence over user content.

Phase 5 addition: EmbeddingGenerator batches rationale text through
text-embedding-3-small and returns 1 536-dim float vectors. Called alongside
classification in main.py; errors are soft-failed so conviction state is never
blocked by an embedding failure.
"""
from __future__ import annotations

import logging
from typing import Sequence

from openai import AzureOpenAI
import json

logger = logging.getLogger(__name__)

# Exactly the 10 conviction states defined in §3.
CONVICTION_STATES: list[str] = [
    "researched_bull",
    "researched_bear",
    "emotional_bull",
    "emotional_bear",
    "uncertainty",
    "earnings_focused",
    "product_thesis",
    "ecosystem_thesis",
    "institutional_watch",
    "exit_signal",
]

# Default system prompt — overridden by Key Vault secret `conviction-prompt-v1`.
# Template variables: {ticker}, {sentiment} only.
# The Reddit post body is sent as a SEPARATE user message (never interpolated here)
# to prevent prompt injection from adversarial post content.
DEFAULT_SYSTEM_PROMPT = """\
Classify the Reddit post (provided by the user) into exactly one conviction state.
Ticker context: {ticker}. Extractor sentiment hint: {sentiment}.

Conviction states:
- researched_bull: cites data, metrics, product or financial evidence for a bullish thesis
- researched_bear: critical thesis with evidence against the stock
- emotional_bull: enthusiasm or hype without substantive evidence
- emotional_bear: fear, panic, or FUD without evidence
- uncertainty: explicitly undecided or confused about the thesis
- earnings_focused: tied to specific upcoming or recent earnings event
- product_thesis: driven by product or technology roadmap belief
- ecosystem_thesis: driven by industry-wide tailwind or macro trend
- institutional_watch: mentions analyst upgrades, price targets, or institutional buying
- exit_signal: profit-taking, covering a position, or conviction loss

Respond with JSON only.\
"""

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "conviction_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "conviction_state": {
                    "type": "string",
                    "enum": CONVICTION_STATES,
                },
                "conviction_confidence": {
                    "type": "number",
                },
            },
            "required": ["conviction_state", "conviction_confidence"],
            "additionalProperties": False,
        },
    },
}


class ConvictionClassifier:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        prompt_template: str,
    ) -> None:
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version="2024-08-01-preview",
        )
        self._deployment = deployment
        self._prompt_template = prompt_template

    def classify(
        self,
        ticker: str,
        sentiment: str,
        rationale: str,
    ) -> tuple[str, float]:
        """Return (conviction_state, conviction_confidence).

        Raises on OpenAI API error — caller decides retry/skip policy.

        Prompt injection defence: instructions are in the system message;
        the untrusted post body is the user message only.
        """
        system_msg = self._prompt_template.format(
            ticker=ticker,
            sentiment=sentiment,
        )
        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": rationale or "(no content)"},
            ],
            response_format=_RESPONSE_FORMAT,  # type: ignore[arg-type]
            max_tokens=64,
            temperature=0.0,
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        state = result.get("conviction_state", "uncertainty")
        confidence = float(result.get("conviction_confidence", 0.5))
        # Clamp confidence to [0, 1] — model may return out-of-range values.
        confidence = max(0.0, min(1.0, confidence))
        if state not in CONVICTION_STATES:
            logger.warning("Unexpected conviction_state %r — defaulting to uncertainty", state)
            state = "uncertainty"
        return state, confidence


# ---------------------------------------------------------------------------
# Phase 5 — embedding generator
# ---------------------------------------------------------------------------

# Default embedding model name — overridden by KV secret embed-deployment.
# text-embedding-ada-002 is 1536-dim; text-embedding-3-large defaults to 3072-dim.
_EMBEDDING_MODEL = "text-embedding-ada-002"
_EMBEDDING_DIMS = 1536
# OpenAI embedding API hard limit per request.
_EMBED_BATCH_LIMIT = 100


class EmbeddingGenerator:
    """Wraps text-embedding-3-small for batch embedding of rationale text.

    Returns a list of 1 536-dim float vectors, one per input text.
    Inputs that exceed the token limit are truncated to 8 191 tokens by the
    API automatically; no client-side truncation needed.

    Error handling: the caller (main.py) wraps calls in a try/except so that
    embedding failures never block conviction-state writes.
    """

    def __init__(self, api_key: str, endpoint: str, deployment: str) -> None:
        # azure_deployment pins the deployment at the client level.
        # Passing model= in embeddings.create() alone is not reliable in some
        # openai SDK versions — it can be ignored and the call misrouted to
        # chat/completions. Setting azure_deployment on the constructor fixes this.
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_version="2024-02-01",
        )
        self._deployment = deployment

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embeddings for each text. Raises on API error.

        Splits into sub-batches of at most _EMBED_BATCH_LIMIT items.
        Empty strings are replaced with a single space to avoid API rejection.
        """
        results: list[list[float]] = []
        safe_texts = [t if t.strip() else " " for t in texts]
        for i in range(0, len(safe_texts), _EMBED_BATCH_LIMIT):
            chunk = safe_texts[i : i + _EMBED_BATCH_LIMIT]
            response = self._client.embeddings.create(
                model=self._deployment,
                input=chunk,
            )
            # API returns items sorted by index.
            chunk_vecs = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            results.extend(chunk_vecs)
        return results
