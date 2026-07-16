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

from database import SessionLocal
from models import Company, Financials

HEADERS = {"User-Agent": "Avyakta Sharma avyaktansharma@gmail.com"}

# metric we are looking for
TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income":[ "NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "equity": ["StockholdersEquity"],
    "debt_current": ["LongTermDebtCurrent"],
    "debt_noncurrent": ["LongTermDebtNoncurrent"],
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

def ingest_company(ticker: str, cik: str, name: str, sector: str | None = None) -> int:
    """Fetch EDGAR data for one company and upsert financials. Returns rows written."""
    facts = fetch_company_facts(cik)

    series = {key: extract_quarterly(facts, tag) for key, tag in TAGS.items()}

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

        written = 0
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

            exists = (
                db.query(Financials)
                .filter(
                    Financials.company_id == company.id,
                    Financials.period_end == period,
                )
                .first()
            )
            if exists:
                continue

            db.add(
                Financials(
                    company_id=company.id,
                    period_end=period,
                    revenue=series["revenue"].get(period),
                    net_income=series["net_income"].get(period),
                    free_cash_flow=fcf,
                    total_debt=total_debt,
                    shareholders_equity=series["equity"].get(period),
                )
            )
            written += 1

        db.commit()
        return written
    finally:
        db.close()


if __name__ == "__main__":
    count = ingest_company("MSFT", "789019", "Microsoft", "Technology")
    print(f"Wrote {count} new financials rows for MSFT")

