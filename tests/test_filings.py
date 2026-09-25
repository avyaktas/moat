"""Tests for filing section extraction.

These test the pure text-processing logic with synthetic documents - no
network calls, so the suite stays fast and doesn't depend on the SEC
being reachable. The synthetic documents reproduce the real-world quirks
found while exploring Microsoft's FY2025 10-K: headings split across
lines, and section names appearing in a table of contents as well as at
the actual section.
"""

from moat.filings import _loose, extract_section


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


# --- the filing text cache ---
#
# get_risk_factors downloads roughly 8MB of inline-XBRL HTML and parses it
# with BeautifulSoup. /brief and /report each did that independently on every
# cache miss, which was the dominant cost of building a report.

from moat import filings


def setup_function():
    filings.clear_filing_caches()


def _stub_filing(monkeypatch, fetches: list, accession: str = "0001-23-456789"):
    monkeypatch.setattr(
        filings,
        "find_latest_10k",
        lambda cik: {
            "url": "https://example.com/10k.htm",
            "filing_date": "2025-07-30",
            "report_date": "2025-06-30",
            "accession": accession,
        },
    )

    def _fetch(url):
        fetches.append(url)
        return "intro ITEM 1A RISK FACTORS the real risks here ITEM 1B rest"

    monkeypatch.setattr(filings, "fetch_clean_text", _fetch)


def test_risk_factors_are_fetched_once(monkeypatch):
    fetches = []
    _stub_filing(monkeypatch, fetches)

    first = filings.get_risk_factors("789019")
    second = filings.get_risk_factors("789019")

    assert first["text"] == second["text"]
    assert len(fetches) == 1, f"downloaded the filing {len(fetches)} times"


def test_a_new_filing_is_a_cache_miss(monkeypatch):
    """Keyed on accession, so a newly filed 10-K cannot serve stale text."""
    fetches = []
    _stub_filing(monkeypatch, fetches, accession="0001-25-000001")
    filings.get_risk_factors("789019")

    _stub_filing(monkeypatch, fetches, accession="0001-26-000002")
    filings.get_risk_factors("789019")

    assert len(fetches) == 2, "a new accession must refetch"


def test_missing_section_is_cached_too(monkeypatch):
    """Re-downloading 8MB to rediscover a non-standard heading helps nobody."""
    fetches = []
    monkeypatch.setattr(
        filings,
        "find_latest_10k",
        lambda cik: {
            "url": "https://example.com/10k.htm",
            "filing_date": "2025-07-30",
            "report_date": "2025-06-30",
            "accession": "acc-1",
        },
    )

    def _fetch(url):
        fetches.append(url)
        return "this filing uses entirely non-standard headings"

    monkeypatch.setattr(filings, "fetch_clean_text", _fetch)

    assert filings.get_risk_factors("789019") is None
    assert filings.get_risk_factors("789019") is None
    assert len(fetches) == 1


def test_latest_10k_lookup_is_cached(monkeypatch):
    calls = []

    def _get(url, **kwargs):
        calls.append(url)

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "filings": {
                        "recent": {
                            "form": ["10-K"],
                            "accessionNumber": ["0001-23-456789"],
                            "primaryDocument": ["d.htm"],
                            "filingDate": ["2025-07-30"],
                            "reportDate": ["2025-06-30"],
                        }
                    }
                }

        return _R()

    monkeypatch.setattr(filings.requests, "get", _get)
    filings.find_latest_10k("789019")
    filings.find_latest_10k("789019")
    assert len(calls) == 1


def test_company_with_no_10k_is_cached_as_none(monkeypatch):
    calls = []

    def _get(url, **kwargs):
        calls.append(url)

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "accessionNumber": ["x"],
                            "primaryDocument": ["d.htm"],
                            "filingDate": ["2025-07-30"],
                            "reportDate": ["2025-06-30"],
                        }
                    }
                }

        return _R()

    monkeypatch.setattr(filings.requests, "get", _get)
    assert filings.find_latest_10k("789019") is None
    assert filings.find_latest_10k("789019") is None
    assert len(calls) == 1, "a genuine 'no 10-K' is an answer worth caching"
