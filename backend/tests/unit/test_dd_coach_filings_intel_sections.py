"""Unit tests for filings_intel.sections — pure HTML section extraction."""
from __future__ import annotations

from services.dd_coach.filings_intel.sections import (
    extract_10q_mda,
    extract_proxy_text,
    extract_sections,
    strip_to_text,
)


_HTML_10K = """
<html><body>
<p>Cover page; table of contents.</p>
<p>Item 1. Business</p>
<p>We design widgets and sell them worldwide. {biz_filler}</p>
<p>Item 1A. Risk Factors</p>
<p>Supply chain risk. Competition risk. Cyber risk. {risk_filler}</p>
<p>Item 1B. Unresolved Staff Comments</p>
<p>None.</p>
<p>Item 2. Properties</p>
<p>HQ in Delaware.</p>
<p>Item 7. Management's Discussion and Analysis of Financial Condition</p>
<p>Revenue rose 12%. {mda_filler}</p>
<p>Item 7A. Quantitative Disclosures</p>
<p>FX exposure.</p>
<p>Item 8. Financial Statements</p>
<p>Audit report follows.</p>
</body></html>
""".format(
    # Pad each section so it survives any future min-length checks.
    biz_filler="Widgets are great. " * 50,
    risk_filler="A persistent threat. " * 50,
    mda_filler="Margins expanded. " * 50,
)


def test_strip_to_text_drops_scripts_and_styles() -> None:
    html = "<html><head><style>x{}</style></head><body><script>1</script><p>Hi</p></body></html>"
    out = strip_to_text(html)
    assert "Hi" in out
    assert "x{}" not in out
    assert "script" not in out.lower() or "1" not in out


def test_extract_sections_picks_three_slices() -> None:
    s = extract_sections(_HTML_10K)
    assert "design widgets" in s.business
    assert "Item 1A" not in s.business  # ends before risk factors
    assert "Supply chain risk" in s.risk_factors
    assert "Item 2" not in s.risk_factors
    assert "Revenue rose 12%" in s.mda
    assert "Item 8" not in s.mda


def test_extract_sections_missing_markers_returns_empty_slices() -> None:
    s = extract_sections("<html><body><p>No items here.</p></body></html>")
    assert s.business == ""
    assert s.risk_factors == ""
    assert s.mda == ""
    assert s.is_empty()


def test_extract_10q_mda_returns_only_mda() -> None:
    html = """
    <html><body>
    <p>Item 7. Management's Discussion and Analysis of Financial Condition</p>
    <p>Q3 results beat consensus. {filler}</p>
    <p>Item 8. Financial Statements</p>
    </body></html>
    """.format(filler="Detail. " * 100)
    mda = extract_10q_mda(html)
    assert "Q3 results beat" in mda
    assert "Item 8" not in mda


def test_extract_proxy_text_truncates() -> None:
    html = "<html><body><p>" + ("Executive compensation. " * 5000) + "</p></body></html>"
    out = extract_proxy_text(html, max_chars=1000)
    assert len(out) <= 1000
    assert "Executive compensation" in out
