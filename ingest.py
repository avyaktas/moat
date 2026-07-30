"""This file is the EDGAR ingestion pipeline: it fetches real filled financials
from the SEC and writes it into the database.

1. FETCH (fetch_company_facts)
    Calls the SEC's EDGAR API for a company, udentified by its CIK.
    THe CIK it the SEC's company ID, 0-padded to 10 digits. 
    Then it returns a JSON containing every numeric fact the comapany has
    ever filed (revenue, income, assets, etc.) across all years and filings. 
    no API key needed but the SEC needs my name and emial. 
    raise_for_status() makes a bad HTTP response fail loudly. 
    
2. EXTRACT (extract_quarterly)
    The raw JSOn has a GAAP tag for each concept and the same period
    can appear many times, so keep only the entries that are filed
    quarterly. It uses the SEC's canonical-period marker which filter
    the annual duplicates. The fates arrive as strings and then parsed into 
    python date objects. 
    Output per tag: {period_end_date: value}
    {} if gaps in data
    
3. TRANSFORM (part of ingest_company)
    TAGS dict maps col names to SEC's GAAP tag names. 
    Two metric need to be derived: 
        - free_cash_flow: operating cash flow - capex
        - total_debt: current + noncurrent long term debt
    Take union of all periods seen across all tage, so a period missing
    some metrics still gets a row with honest NULLs.
    
4. LOAD (part of ingest_company)
    Looks up company by ticker, creating it if new. 
    For each period: skip if a row for (company, period) alr exists. 
    Makes scripd idempotent: safe to run repeatedlt, reruns write 0 new rows.
    
"""

import requests
from sqlalchemy.orm import Session
from datetime import date
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import SessionLocal
from models import Company, Financials

HEADERS = {"User-Agent": "Avyakta Sharma avyaktansharma@gmail.com"}

# metric we are looking for

# split in two se we can get Q4
FLOW_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}

SNAPSHOT_TAGS = {
    "equity": ["StockholdersEquity"],
    "debt_current": ["LongTermDebtCurrent"],
    "debt_noncurrent": ["LongTermDebtNoncurrent"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
}

def fetch_company_facts(cik: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:>010}.json"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()

# REPL discovery
def extract_quarterly(facts: dict, tags: list[str]) -> dict[date, float]:
    """Return {period_end: value}, trying each candidate tag in order"""

    out = {}
    for tag in tags:
        try:
            entries = facts["facts"]["us-gaap"][tag]["units"]["USD"]
        except KeyError:
            continue
        for e in entries:
            if "frame" in e and "Q" in e.get("frame", ""):
                out.setdefault(date.fromisoformat(e["end"]), e["val"])
    return out

def extract_annual(facts: dict, tags: list[str]) -> dict[date, tuple[date, float]]:
    """Return {period_end: (period_start, value)} for annual (~365-day) entries.

    Annual figures come from 10-Ks and carry no calendar frame when the
    fiscal year is off-calendar, so we identify them by duration instead.
    The 350–380 day window tolerates 52/53-week fiscal calendars.
    """
    out = {}
    for tag in tags:
        try:
            entries = facts["facts"]["us-gaap"][tag]["units"]["USD"]
        except KeyError:
            continue
        for e in entries:
            if e.get("form") != "10-K" or "start" not in e:
                continue
            start = date.fromisoformat(e["start"])
            end = date.fromisoformat(e["end"])
            if 350 <= (end - start).days <= 380:
                out.setdefault(end, (start, e["val"]))
    return out

# Duration windows, in days, for reasoning about reporting periods.
# Fiscal quarters run ~13 weeks (91 days), occasionally 14 (98) in 53-week
# years; the gap up to a half-year (~180) is wide, so these windows are
# unambiguous.
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100
_INTERIM_MAX_DAYS = 300   # Q1-Q3 spans; the annual (~365) is left to derive_q4


def extract_ytd(facts: dict, tags: list[str]) -> list[tuple[date, date, float]]:
    """Return [(period_start, period_end, value)] for a flow tag's duration
    entries, first candidate tag winning any (start, end) it fills.

    Cumulative filers (Apple, IBM) report cash flow year-to-date within the
    fiscal year, so their Q2 and Q3 exist only as running totals from the
    fiscal-year start. Those are exactly the entries extract_quarterly
    discards for lack of a standalone-quarter frame; derive_interim_quarters
    differences them back into standalone quarters.
    """
    seen: dict[tuple[date, date], float] = {}
    for tag in tags:
        try:
            entries = facts["facts"]["us-gaap"][tag]["units"]["USD"]
        except KeyError:
            continue
        for e in entries:
            if e.get("form") not in ("10-Q", "10-K") or "start" not in e:
                continue
            start = date.fromisoformat(e["start"])
            end = date.fromisoformat(e["end"])
            seen.setdefault((start, end), e["val"])
    return [(s, en, v) for (s, en), v in seen.items()]


def derive_interim_quarters(
    quarterly: dict[date, float],
    ytd: list[tuple[date, date, float]],
) -> dict[date, float]:
    """Recover standalone interim quarters (Q2, Q3) from a year-to-date chain.

    Within a fiscal year every YTD entry shares one start date; sorting by
    end gives a chain of running totals - Q1, then Q1+Q2, then Q1+Q2+Q3.
    Consecutive differences are the standalone quarters:
        Q2 = YTD(Q2) - YTD(Q1),   Q3 = YTD(Q3) - YTD(Q2).

    This is the same real-arithmetic-on-filed-numbers principle as derive_q4,
    and like it, refuses to guess. Two chain members are differenced ONLY when
    their durations are exactly one quarter apart. A missing middle period
    makes that step ~half a year, the guard fails, and the quarter stays
    absent - never estimated. Existing standalone quarters (discrete filers
    already publish them) are preserved via setdefault, so on a filer that
    doesn't need this the whole pass is a no-op. That structural guard, not a
    ticker list, is what "detects" a cumulative filer.
    """
    out = dict(quarterly)

    chains: dict[date, list[tuple[date, float]]] = {}
    for start, end, val in ytd:
        chains.setdefault(start, []).append((end, val))

    for start, members in chains.items():
        members.sort(key=lambda m: m[0])  # by end -> ascending duration
        prev_val = prev_days = None
        for end, val in members:
            days = (end - start).days
            if prev_days is None:
                # First member is a standalone quarter only if it spans ~one.
                if _QUARTER_MIN_DAYS <= days <= _QUARTER_MAX_DAYS:
                    out.setdefault(end, val)
            elif (days <= _INTERIM_MAX_DAYS
                  and _QUARTER_MIN_DAYS <= days - prev_days <= _QUARTER_MAX_DAYS):
                out.setdefault(end, val - prev_val)
            prev_val, prev_days = val, days
    return out


def derive_q4(quarterly: dict[date, float],
              annual: dict[date, tuple[date, float]]) -> dict[date, float]:
    """Fill in missing Q4 values: Q4 = FY - (Q1 + Q2 + Q3).

    Companies don't file a standalone Q4 10-Q; the fourth quarter lives
    inside the annual 10-K figure. Where we have the full year and exactly
    three quarters inside it, the fourth is recoverable by subtraction -
    real filed arithmetic, not an estimate. If any quarter is missing we
    derive nothing rather than guess.
    """
    out = dict(quarterly)
    for fy_end, (fy_start, fy_val) in annual.items():
        if fy_end in out:
            continue
        covered = [v for p, v in quarterly.items() if fy_start <= p <= fy_end]
        if len(covered) == 3:
            out[fy_end] = fy_val - sum(covered)
    return out

_ticker_cache: dict[str, tuple[str, str]] | None = None

def get_cik(ticker: str) -> tuple[str, str]:
    """Look up (CIK, name) for a ticker from the SEC's mapping file.
    
    The ~10-K entry file is fetched once per process and cached in memory - 
    it changes rarely and refetching it every ingest would hammer the SEC.
    ValueErrors raised for tickers that are not in the file."""

    global _ticker_cache
    if _ticker_cache is None:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS, timeout=30,
        )
        resp.raise_for_status()
        _ticker_cache = {
            v["ticker"].upper(): (str(v["cik_str"]), v["title"])
            for v in resp.json().values()
        }
    ticker = ticker.upper()
    if ticker not in _ticker_cache:
        raise ValueError(f"Unknown ticker: {ticker}")
    return _ticker_cache[ticker]

def ingest_company(ticker: str, sector: str | None = None) -> int:
    """Fetch EDGAR data for one company and upsert financials. Returns rows written."""
    cik, name = get_cik(ticker)
    facts = fetch_company_facts(cik)

    series = {}
    for key, tags in FLOW_TAGS.items():
        # Standalone quarters, then fill cumulative filers' interim Q2/Q3 by
        # differencing the YTD chain, then derive the fourth quarter.
        quarterly = extract_quarterly(facts, tags)
        quarterly = derive_interim_quarters(quarterly, extract_ytd(facts, tags))
        series[key] = derive_q4(quarterly, extract_annual(facts, tags))
    for key, tags in SNAPSHOT_TAGS.items():
        series[key] = extract_quarterly(facts, tags)
    all_periods = set()
    for s in series.values():
        all_periods.update(s.keys())

    db: Session = SessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == ticker).first()
        if company is None:
            company = Company(ticker=ticker, name=name, sector=sector)
            db.add(company)
            db.flush()

        processed = 0
        for period in sorted(all_periods):
            ocf = series["operating_cash_flow"].get(period)
            capex = series["capex"].get(period)
            fcf = (ocf - capex) if (ocf is not None and capex is not None) else None

            dc = series["debt_current"].get(period)
            dnc = series["debt_noncurrent"].get(period)
            if dc is not None or dnc is not None:
                total_debt = (dc or 0) + (dnc or 0)
            else:
                total_debt = None

            values = {
                "company_id": company.id,
                "period_end": period,
                "revenue": series["revenue"].get(period),
                "net_income": series["net_income"].get(period),
                "free_cash_flow": fcf,
                "total_debt": total_debt,
                "shareholders_equity": series["equity"].get(period),
                "cash": series["cash"].get(period),
                "short_term_investments": series["short_term_investments"].get(period),
            }

            stmt = pg_insert(Financials).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_company_period",
                set_={k: v for k, v in values.items() if k not in ("company_id", "period_end")},
            )
            db.execute(stmt)
            processed += 1

        db.commit()
        return processed
    finally:
        db.close()


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    count = ingest_company(ticker)
    print(f"Processed {count} periods for {ticker}")

