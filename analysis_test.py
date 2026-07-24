"""Tests for grounding verification.

These test the quote-checking logic, which is the part that makes the
evaluation objective. No API calls - the LLM is mocked or bypassed, so
the suite stays fast and costs nothing to run.
"""

from analysis import check_quote, compact, grounding_rate, normalize

SOURCE = (
    "ITEM 1A. RIS\nK FACTORS\n"
    "We face intense competition across all markets for our products "
    "and services.\xa0Our competitors range in size from diversified "
    "global companies to small, specialized firms."
)


def test_normalize_collapses_whitespace():
    assert normalize("a   b\n\nc") == "a b c"


def test_normalize_handles_nonbreaking_space():
    assert normalize("a\xa0b") == "a b"


def test_check_quote_finds_exact_match():
    assert check_quote("We face intense competition", SOURCE) is True


def test_check_quote_tolerates_whitespace_differences():
    # The source has a non-breaking space; the model quotes a normal one.
    assert check_quote("and services. Our competitors range", SOURCE) is True


def test_check_quote_tolerates_word_split_across_lines():
    assert check_quote("ITEM 1A. RISK FACTORS", SOURCE) is True


def test_check_quote_rejects_fabrication():
    # Plausible, true in the real world, absent from this document.
    assert check_quote("We compete with Google and Amazon", SOURCE) is False


def test_grounding_rate_all_grounded():
    quotes = ["We face intense competition", "small, specialized firms"]
    assert grounding_rate(quotes, SOURCE) == 1.0


def test_grounding_rate_partial():
    quotes = ["We face intense competition", "We compete with Google"]
    assert grounding_rate(quotes, SOURCE) == 0.5


def test_grounding_rate_none_when_no_quotes():
    # An unanswered question has nothing to ground - that is not 0%.
    assert grounding_rate([], SOURCE) is None


def test_compact_removes_all_whitespace():
    assert compact("A  B\nC") == "abc"