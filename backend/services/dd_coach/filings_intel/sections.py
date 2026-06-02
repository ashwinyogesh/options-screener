"""Pure section extractors for 10-K / 10-Q filing HTML.

Stateless, side-effect-free. Extracts Item 1 (Business), Item 1A (Risk
Factors) and Item 7 (MD&A) as separate strings — finer-grained than the
supply_chain extractor, because the LLM insight prompts need each
section in isolation.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# Section markers — filings vary in spacing/punctuation; matchers are lenient.
_RE_ITEM1 = re.compile(r"(?is)\bitem\s*1[.\s]\s*business\b")
_RE_ITEM1A = re.compile(r"(?is)\bitem\s*1a[.\s]\s*risk\s+factors\b")
_RE_ITEM1B = re.compile(r"(?is)\bitem\s*1b[.\s]")
_RE_ITEM2 = re.compile(r"(?is)\bitem\s*2[.\s]\s*properties\b")
_RE_ITEM7 = re.compile(r"(?is)\bitem\s*7[.\s]\s*management.s\s+discussion")
_RE_ITEM7A = re.compile(r"(?is)\bitem\s*7a[.\s]")
_RE_ITEM8 = re.compile(r"(?is)\bitem\s*8[.\s]\s*financial\s+statements")

_RE_BLANK_LINES = re.compile(r"\n{2,}")
_RE_RUNS_OF_SPACES = re.compile(r"[ \t]+")

# Per-section soft caps (chars). Sized for gpt-4o 128k context with headroom
# for the system prompt + structured-output schema overhead.
MAX_BUSINESS_CHARS = 60_000
MAX_RISK_CHARS = 80_000
MAX_MDA_CHARS = 50_000


@dataclass(frozen=True)
class FilingSections:
    business: str
    risk_factors: str
    mda: str

    def is_empty(self) -> bool:
        return not (self.business or self.risk_factors or self.mda)


def strip_to_text(html: str) -> str:
    """Strip HTML/script/style, return whitespace-normalised text."""
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    text = soup.get_text(separator="\n")
    text = _RE_BLANK_LINES.sub("\n\n", text)
    text = _RE_RUNS_OF_SPACES.sub(" ", text)
    return text


def _slice(text: str, start_re: re.Pattern[str], end_res: list[re.Pattern[str]]) -> str:
    m_start = start_re.search(text)
    if not m_start:
        return ""
    start = m_start.start()
    # Pick the earliest end-marker that occurs after `start`.
    candidates = [m.start() for r in end_res if (m := r.search(text, pos=start + 1))]
    end = min(candidates) if candidates else len(text)
    return text[start:end].strip()


def extract_sections(html: str) -> FilingSections:
    """Return Business / Risk Factors / MD&A slices from a 10-K HTML body.

    Each section is independently extracted; if a section's start marker is
    missing the section is returned as ``""``. Slices are tail-trimmed to
    per-section soft caps.
    """
    text = strip_to_text(html)
    business = _slice(text, _RE_ITEM1, [_RE_ITEM1A, _RE_ITEM1B, _RE_ITEM2])
    risk_factors = _slice(text, _RE_ITEM1A, [_RE_ITEM1B, _RE_ITEM2])
    mda = _slice(text, _RE_ITEM7, [_RE_ITEM7A, _RE_ITEM8])

    if business and len(business) > MAX_BUSINESS_CHARS:
        business = business[:MAX_BUSINESS_CHARS]
    if risk_factors and len(risk_factors) > MAX_RISK_CHARS:
        risk_factors = risk_factors[:MAX_RISK_CHARS]
    if mda and len(mda) > MAX_MDA_CHARS:
        mda = mda[:MAX_MDA_CHARS]

    if not (business or risk_factors or mda):
        logger.warning("extract_sections: no section markers matched (len=%d)", len(text))
    return FilingSections(business=business, risk_factors=risk_factors, mda=mda)


def extract_10q_mda(html: str) -> str:
    """Return only the MD&A slice from a 10-Q HTML body."""
    text = strip_to_text(html)
    mda = _slice(text, _RE_ITEM7, [_RE_ITEM7A, _RE_ITEM8])
    if mda and len(mda) > MAX_MDA_CHARS:
        mda = mda[:MAX_MDA_CHARS]
    return mda


def extract_proxy_text(html: str, max_chars: int = 120_000) -> str:
    """Return stripped text from a DEF 14A; head-trimmed to ``max_chars``.

    The Summary Compensation Table + CEO Pay Ratio sit roughly halfway
    through most proxies; head-trim is good enough at the cap we use.
    """
    text = strip_to_text(html)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text
