"""Fetch and extract narrative sections from SEC 10-K filings.

The numbers in a 10-K come from the XBRL API (see ingest.py); this module
handles the *prose* - Business, Risk Factors, MD&A - which only exists in
the filed HTML document.

WHY EXTRACTION IS NECESSARY
    A modern 10-K is ~8MB of inline-XBRL HTML (~2M tokens). Stripping the
    markup gets it to ~400K characters - still far too large for a model
    context, and mostly irrelevant to any given question. Because 10-K
    sections are standardized by regulation (Item 1A is always Risk
    Factors), we can slice out the one section that matters: ~69K
    characters for Microsoft's FY2025 risk factors, a 99% reduction.
    That's why this module uses structured section extraction rather than
    embedding the whole document and hoping similarity search finds the
    right passages.

WHY THE MATCHING IS LOOSE
    Filers' HTML splits words across tags for styling, so "RISK FACTORS"
    can arrive from the text extractor as "RIS\nK FACTORS". Patterns
    therefore tolerate whitespace between every character.

WHY WE TAKE THE LONGEST SPAN
    A section heading appears several times in a filing: in the table of
    contents, in cross-references ("see Item 1A"), and at the actual
    section. The real section is the one with the most text before the
    next boundary, so we choose the longest candidate span rather than
    guessing at position.
"""

import logging
import re
import warnings

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

import timing
from cache import TTLCache

HEADERS = {"User-Agent": "Avyakta Sharma avyaktansharma@gmail.com"}

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:>010}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

# A company files one 10-K a year, so "which is the latest" is close to
# immutable. It is checked on every cached brief to decide whether that brief
# is still answering the current filing, and without a cache that freshness
# check would cost an SEC round trip on the hot path of every request.
LATEST_10K_TTL_SECONDS = 3600

# The Risk Factors text is the expensive one. Fetching it means downloading
# roughly 8MB of inline-XBRL HTML and parsing it with BeautifulSoup, and both
# /brief and /report did that independently on every cache miss. It is also
# the most cacheable thing here: a 10-K does not change after it is filed.
#
# Entries are large - around 70KB of extracted text each - so this is capped
# tightly. 64 companies is far more than this service sees concurrently, and
# bounds the worst case at a few megabytes rather than at however many tickers
# someone cares to enumerate.
FILING_TEXT_TTL_SECONDS = 6 * 3600
FILING_TEXT_MAX_ENTRIES = 64

_latest_10k_cache = TTLCache(ttl_seconds=LATEST_10K_TTL_SECONDS, max_entries=512)
_risk_factors_cache = TTLCache(
    ttl_seconds=FILING_TEXT_TTL_SECONDS, max_entries=FILING_TEXT_MAX_ENTRIES
)


def clear_filing_caches() -> None:
    """Drop cached filing metadata and text. Used by tests to isolate cases."""
    _latest_10k_cache.clear()
    _risk_factors_cache.clear()

def _loose(phrase: str) -> re.Pattern:
    """Build a regex matching a phrase with arbitrary whitespace anywhere.

    "ITEM 1A RISK FACTORS" becomes a pattern that also matches
    "ITEM  1A.\nRIS\nK FACTORS" - necessary because filer HTML splits
    words across tags and the text extractor preserves those breaks.
    """
    chars = [re.escape(c) for c in phrase if not c.isspace()]
    return re.compile(r"[\s.]*".join(chars), re.IGNORECASE)


def find_latest_10k(cik: str) -> dict | None:
    """Return metadata for a company's most recent 10-K, or None if none exists.

    The submissions endpoint returns filings as parallel lists - form[i],
    accessionNumber[i], and primaryDocument[i] all describe filing i - so
    we find the indices of 10-K forms and take the first (most recent).

    Returns a dict with url, filing_date, report_date, and accession.

    Cached for LATEST_10K_TTL_SECONDS. Only successful lookups are cached; a
    failure raises and is not remembered, so an SEC outage does not pin this
    company to "no filing" for the life of the process.
    """
    if _latest_10k_cache.has(cik):
        return _latest_10k_cache.get(cik)

    resp = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    recent = resp.json()["filings"]["recent"]

    indices = [i for i, form in enumerate(recent["form"]) if form == "10-K"]
    if not indices:
        # A genuine "this company has never filed a 10-K" is worth caching;
        # it is an answer, not a failure.
        _latest_10k_cache.set(cik, None)
        return None

    i = indices[0]
    # Accession numbers carry dashes in the API but not in archive URL paths.
    accession = recent["accessionNumber"][i].replace("-", "")

    filing = {
        "url": ARCHIVE_URL.format(
            cik=cik.lstrip("0"),
            accession=accession,
            document=recent["primaryDocument"][i],
        ),
        "filing_date": recent["filingDate"][i],
        "report_date": recent["reportDate"][i],
        "accession": recent["accessionNumber"][i],
    }
    _latest_10k_cache.set(cik, filing)
    return filing


def fetch_clean_text(url: str) -> str:
    """Download a filing and return its text with HTML markup removed.

    Collapses runs of blank lines and spaces, which HTML tables and layout
    produce in abundance and which make section matching less reliable.
    """
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(separator="\n")

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def extract_section(text: str, start_phrase: str, end_phrase: str) -> str | None:
    """Return the text between two section headings, or None if not found.

    Both phrases may match several times (table of contents, cross
    references, the real heading). For each start match we measure the
    span to the next end match after it, and return the longest such span
    - the real section contains far more text than a table-of-contents
    entry or a passing reference.
    """
    starts = [m.start() for m in _loose(start_phrase).finditer(text)]
    ends = [m.start() for m in _loose(end_phrase).finditer(text)]
    if not starts or not ends:
        return None

    best = None
    for start in starts:
        following = [e for e in ends if e > start]
        if not following:
            continue
        span = text[start:following[0]]
        if best is None or len(span) > len(best):
            best = span

    return best


def get_risk_factors(cik: str) -> dict | None:
    """Fetch a company's latest 10-K and return its Risk Factors section.

    Returns a dict with the section text plus filing metadata, or None if
    no 10-K exists or the section could not be located (some filers use
    non-standard headings; an honest None beats a wrong slice).

    Cached on the filing's accession number rather than on the CIK, so a
    newly filed 10-K is a cache miss by construction: the key changes when
    the document does, and there is no window where a stale section is served
    for a filing that has been superseded.
    """
    filing = find_latest_10k(cik)
    if filing is None:
        return None

    key = filing["accession"]
    if _risk_factors_cache.has(key):
        return _risk_factors_cache.get(key)

    with timing.stage("edgar.filing_download"):
        text = fetch_clean_text(filing["url"])
    with timing.stage("edgar.filing_extract"):
        section = extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B")
    if section is None:
        # Cache the miss too. Re-downloading 8MB on every request to rediscover
        # that this filer uses non-standard headings helps nobody.
        _risk_factors_cache.set(key, None)
        return None

    result = {
        "section": "Risk Factors",
        "text": section,
        "url": filing["url"],
        "filing_date": filing["filing_date"],
        "report_date": filing["report_date"],
    }
    _risk_factors_cache.set(key, result)
    return result


if __name__ == "__main__":
    import sys

    cik = sys.argv[1] if len(sys.argv) > 1 else "789019"
    result = get_risk_factors(cik)
    if result is None:
        print("No risk factors found.")
    else:
        print(f"Report date: {result['report_date']}")
        print(f"URL: {result['url']}")
        print(f"Length: {len(result['text']):,} characters")
        print(f"\nFirst 400 chars:\n{result['text'][:400]}")