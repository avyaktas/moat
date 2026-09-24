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

# --- the empty-quote hole ---
#
# `"" in anything` is True, so an empty or whitespace-only quote used to pass
# the grounding check and count toward a 100% grounding rate. A quote that
# carries no text cannot support a claim; it is absent, not verified.

def test_check_quote_rejects_empty_quote():
    assert check_quote("", SOURCE) is False


def test_check_quote_rejects_whitespace_only_quote():
    assert check_quote("   \n\t", SOURCE) is False
    assert check_quote("\xa0", SOURCE) is False


def test_check_quote_rejects_punctuation_only_quote():
    # compact() strips the trailing punctuation, leaving nothing behind.
    assert check_quote("...", SOURCE) is False
    assert check_quote(".,;:", SOURCE) is False


def test_grounding_rate_counts_empty_quotes_as_ungrounded():
    # The failure this guards: two empty strings scoring a perfect 1.0.
    assert grounding_rate(["", ""], SOURCE) == 0.0


def test_grounding_rate_mixes_empty_and_real_quotes():
    assert grounding_rate(["We face intense competition", ""], SOURCE) == 0.5


def test_short_quote_still_matches_documented_behaviour():
    """A very short quote matches trivially - this pins current behaviour.

    Raising a minimum-length floor would move the eval numbers, so it is a
    deliberate open question rather than a silent change. This test exists so
    that if the floor is ever introduced, it fails loudly here first.
    """
    assert check_quote("We", SOURCE) is True


# --- typographic punctuation ---
#
# Filings are typeset: they use curly quotes, curly apostrophes, en and em
# dashes, and bullets. A model quoting them types ASCII. The document contains
# (“NOPAs”) and the model wrote ("NOPAs"), so a quote that genuinely appears
# in the filing was reported as fabricated - a false negative in the one check
# the project's credibility rests on.
#
# This is the same class of problem as the non-breaking space compact()
# already forgives: a difference in rendering, not in content. No fabrication
# survives it, because inventing prose that matches the source character for
# character apart from quote glyphs is not a realistic failure mode.

TYPOGRAPHIC = (
    "The IRS issued Notices of Proposed Adjustment (“NOPAs”) "
    "regarding Microsoft’s intercompany transfer pricing—a "
    "long-running dispute… covering 2004 to 2013."
)


def test_curly_double_quotes_match_straight_ones():
    assert check_quote('Notices of Proposed Adjustment ("NOPAs")', TYPOGRAPHIC) is True


def test_curly_apostrophe_matches_straight_one():
    assert check_quote("Microsoft's intercompany transfer pricing", TYPOGRAPHIC) is True


def test_em_dash_matches_hyphen():
    assert check_quote("transfer pricing-a long-running dispute", TYPOGRAPHIC) is True


def test_ellipsis_character_matches_three_periods():
    assert check_quote("a long-running dispute... covering 2004", TYPOGRAPHIC) is True


def test_the_reverse_direction_also_matches():
    """Source straight, quote curly - a model may typeset too."""
    source = 'The company\'s "platform" competitors'
    assert check_quote('The company’s “platform” competitors', source) is True


def test_bullets_are_treated_as_layout():
    source = "risks include:\n• competition\n• regulation"
    assert check_quote("competition regulation", source) is True


def test_folding_does_not_forgive_fabrication():
    """The loosening must not make invented content pass."""
    assert check_quote("The IRS issued no adjustments whatsoever", TYPOGRAPHIC) is False
    assert check_quote("Microsoft's dispute covering 2020 to 2024", TYPOGRAPHIC) is False


def test_folding_does_not_collapse_distinct_words():
    assert check_quote("transfer pricing dispute", "transfer pricing agreement") is False


def test_normalize_preserves_typography_for_display():
    """compact() folds for comparison; normalize() is for showing a human the
    text as written, so it must not rewrite the author's punctuation."""
    assert "’" in normalize("Microsoft’s risk")


# --- control characters inside the model's JSON ---
#
# Observed live, on the first real report generated after this audit:
#   SynthesisError: Model response was not valid JSON:
#   Invalid control character at: line 36 column 864
#
# json.loads rejects raw control characters inside strings by default. A model
# quoting a filing has every reason to emit one: the passages it copies span
# lines, and it writes the line break literally rather than escaping it. The
# whole response is then discarded over a character that carries no meaning.

def test_answer_question_parses_a_raw_newline_in_a_quote():
    import analysis

    quote = "We face intense competition"
    reply = (
        '{"addressed": true, "answer": "Competition is a risk' + chr(10) + 'across markets",'
        ' "quotes": ["' + quote + '"]}'
    )

    class _Stub:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            class _B:
                type = "text"
                text = reply

            class _R:
                content = [_B()]

            return _R()

    result = analysis.answer_question("q", SOURCE, client=_Stub())
    assert result["addressed"] is True
    assert result["quotes"] == [quote]
    assert chr(10) in result["answer"]


def test_answer_question_still_reports_genuinely_broken_json():
    """Tolerating control characters must not swallow a real parse failure."""
    import analysis

    class _Stub:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            class _B:
                type = "text"
                text = "I'm afraid I can't help with that."

            class _R:
                content = [_B()]

            return _R()

    result = analysis.answer_question("q", SOURCE, client=_Stub())
    assert result["addressed"] is None
    assert "error" in result
