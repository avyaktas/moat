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

import re
import warnings

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

HEADERS = {"User-Agent": "Avyakta Sharma avyaktansharma@gmail.com"}

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:>010}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

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
    """
    resp = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    recent = resp.json()["filings"]["recent"]

    indices = [i for i, form in enumerate(recent["form"]) if form == "10-K"]
    if not indices:
        return None

    i = indices[0]
    # Accession numbers carry dashes in the API but not in archive URL paths.
    accession = recent["accessionNumber"][i].replace("-", "")

    return {
        "url": ARCHIVE_URL.format(
            cik=cik.lstrip("0"),
            accession=accession,
            document=recent["primaryDocument"][i],
        ),
        "filing_date": recent["filingDate"][i],
        "report_date": recent["reportDate"][i],
        "accession": recent["accessionNumber"][i],
    }


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
    """
    filing = find_latest_10k(cik)
    if filing is None:
        return None

    text = fetch_clean_text(filing["url"])
    section = extract_section(text, "ITEM 1A RISK FACTORS", "ITEM 1B")
    if section is None:
        return None

    return {
        "section": "Risk Factors",
        "text": section,
        "url": filing["url"],
        "filing_date": filing["filing_date"],
        "report_date": filing["report_date"],
    }


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