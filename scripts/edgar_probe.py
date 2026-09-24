"""Ad-hoc probe: what does EDGAR actually return for a company?

This was test_edgar.py. It contained no test functions - just module-level
code that fired a live request to the SEC. pytest imports every file matching
test_*.py to look for tests, so that request ran on every single collection,
in CI included: a network call to a third party on every run, contributing
zero coverage, and one SEC outage away from failing a build for reasons
entirely unrelated to the code under test.

It is genuinely useful for exploring the XBRL shape by hand, which is what it
was written for, so it is kept - just somewhere pytest does not import it.

Run:  python scripts/edgar_probe.py [CIK]
"""

import sys

import requests

HEADERS = {"User-Agent": "Avyakta Sharma avyaktansharma@gmail.com"}


def main() -> int:
    cik = sys.argv[1] if len(sys.argv) > 1 else "0000789019"
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:>010}.json"
    response = requests.get(url, headers=HEADERS, timeout=30)
    print(response.status_code)
    response.raise_for_status()

    data = response.json()
    print(data["entityName"])
    print(list(data["facts"]["us-gaap"].keys())[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
