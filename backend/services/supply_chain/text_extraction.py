"""Pure text-extraction helpers for SEC filings.

Stateless, side-effect-free. Unit-testable without mocks.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 10-K section markers — filings vary widely in casing/whitespace, so all
# matchers are deliberately lenient.
_RE_ITEM1_BUSINESS_START = re.compile(r"(?is)\bitem\s*1\b[.\s]\s*business\b")
_RE_ITEM2_PROPERTIES_START = re.compile(r"(?is)\bitem\s*2\b[.\s]\s*properties\b")
_RE_ITEM7_MDA_START = re.compile(r"(?is)\bitem\s*7\b[.\s]\s*management.s\s+discussion")
_RE_ITEM8_FINANCIALS_START = re.compile(r"(?is)\bitem\s*8\b[.\s]\s*financial\s+statements")

# Whitespace normalisation
_RE_BLANK_LINES = re.compile(r"\n{2,}")
_RE_RUNS_OF_SPACES = re.compile(r"[ \t]+")

# Minimum slice lengths below which we consider the extraction failed
# and either skip or fall back. Tuned to current heuristics.
_MIN_BUSINESS_SLICE_CHARS = 5_000
_MIN_MDA_SLICE_CHARS = 3_000


def _strip_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    text = soup.get_text(separator="\n")
    text = _RE_BLANK_LINES.sub("\n\n", text)
    text = _RE_RUNS_OF_SPACES.sub(" ", text)
    return text


def extract_10k_relevant_text(html: str, max_chars: int = 600_000) -> str:
    """
    Strip tags, keep the Business + Risk Factors + MD&A sections.

    Slices ``[Item 1 Business → Item 2 Properties]`` and
    ``[Item 7 MD&A → Item 8 Financials]`` and concatenates them. Falls
    back to the full text (tail-trimmed to ``max_chars``) when section
    markers can't be located. This is where supplier/customer/competitor
    info lives.
    """
    text = _strip_to_text(html)

    m_biz_start = _RE_ITEM1_BUSINESS_START.search(text)
    m_biz_end = _RE_ITEM2_PROPERTIES_START.search(text)
    m_mda_start = _RE_ITEM7_MDA_START.search(text)
    m_mda_end = _RE_ITEM8_FINANCIALS_START.search(text)

    parts: list[str] = []
    if m_biz_start and m_biz_end and m_biz_end.start() > m_biz_start.start():
        biz = text[m_biz_start.start(): m_biz_end.start()]
        if len(biz) > _MIN_BUSINESS_SLICE_CHARS:
            parts.append(biz)
        else:
            logger.warning("Item 1 slice too short (%d chars)", len(biz))
    if m_mda_start and m_mda_end and m_mda_end.start() > m_mda_start.start():
        mda = text[m_mda_start.start(): m_mda_end.start()]
        if len(mda) > _MIN_MDA_SLICE_CHARS:
            parts.append(mda)
        else:
            logger.warning("Item 7 slice too short (%d chars)", len(mda))

    if parts:
        text = "\n\n".join(parts)
    else:
        logger.warning("No section slices located - using full text")

    if len(text) > max_chars:
        # Trim from the start - early pages are usually TOC, cover, glossary
        text = text[len(text) - max_chars:]
    return text


def extract_8k_text(html: str, max_chars: int = 30_000) -> str:
    """8-Ks are short; just strip tags and return the whole document."""
    text = _strip_to_text(html)
    return text[:max_chars]
