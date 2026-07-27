"""Tests for filing section extraction.

These test the pure text-processing logic with synthetic documents - no
network calls, so the suite stays fast and doesn't depend on the SEC
being reachable. The synthetic documents reproduce the real-world quirks
found while exploring Microsoft's FY2025 10-K: headings split across
lines, and section names appearing in a table of contents as well as at
the actual section.
"""

from filings import _loose, extract_section


def test_loose_matches_exact_phrase():
    assert _loose("ITEM 1A").search("ITEM 1A") is not None


def test_loose_matches_split_word():
    # Filer HTML splits words across tags; the extractor preserves the break.
    assert _loose("RISK FACTORS").search("RIS\nK FACTORS") is not None


def test_loose_is_case_insensitive():
    assert _loose("ITEM 1A").search("item 1a") is not None


def test_loose_ignores_extra_whitespace():
    assert _loose("ITEM 1A").search("ITEM   1A") is not None


def test_extract_section_basic():
    text = "intro ITEM 1A RISK FACTORS the real risks here ITEM 1B rest"
    section = extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B")
    assert section is not None
    assert "the real risks here" in section
    assert "rest" not in section


def test_extract_section_prefers_longest_span():
    # The first occurrence is a table-of-contents entry followed almost
    # immediately by the next heading; the second is the real section.
    text = (
        "ITEM 1A RISK FACTORS 16 ITEM 1B 30 "  # table of contents
        + "ITEM 1A RISK FACTORS "
        + "actual risk content " * 50
        + "ITEM 1B unresolved staff comments"
    )
    section = extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B")
    assert section is not None
    assert "actual risk content" in section


def test_extract_section_missing_start_returns_none():
    text = "this filing has no such heading ITEM 1B something"
    assert extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B") is None


def test_extract_section_missing_end_returns_none():
    text = "ITEM 1A RISK FACTORS but the document ends here"
    assert extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B") is None


def test_extract_section_end_before_start_returns_none():
    text = "ITEM 1B comes first ... ITEM 1A RISK FACTORS with nothing after"
    assert extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B") is None

def test_loose_matches_real_msft_heading():
    assert _loose("ITEM 1A RISK FACTORS").search("ITEM 1A. RIS\nK FACTORS") is not None