# Moat

A financial analysis platform that ingests SEC EDGAR filings for any US-listed
company and computes the metrics a value investor actually cares about — ROIC,
FCF margin, TTM returns — served through a REST API. Type a ticker, get up to
19 years of analyzed fundamentals computed from primary-source filings.

Built from scratch as a learning project: every layer hand-written and
understood, no scaffolding.

## Example

`GET /company/MSFT/metrics` returns per-quarter ratios plus trailing-twelve-month
aggregates, computed from Microsoft's actual filed financials:

```json
"ttm": {
  "revenue": 318273000000,
  "net_income": 125216000000,
  "free_cash_flow": 72916000000,
  "net_margin": 0.393,
  "fcf_margin": 0.229,
  "roe": 0.302,
  "roic": 0.275
}
```

Those figures cross-check against published Microsoft numbers — because they
come from the same source: EDGAR filings, ingested and derived by this pipeline.

## Quickstart (Docker)
 
Requires only Docker.
 
```bash
docker compose up --build            # builds the API image, starts API + Postgres
docker compose exec api alembic upgrade head   # create the schema
docker compose exec api python ingest.py MSFT  # ingest any ticker
```
 
Then open http://localhost:8000/docs for the interactive API, or hit
http://localhost:8000/company/MSFT/metrics directly.

## Architecture

```
SEC EDGAR API ──> ingest.py ──> PostgreSQL ──> FastAPI ──> JSON
  companyfacts     - ticker→CIK    - companies     - /companies
  company_tickers  - quarterly     - financials    - /company/{ticker}
                     extraction      (Alembic        - /company/{ticker}/financials
                   - Q4 derivation    migrations)    - /company/{ticker}/metrics
                   - FCF/debt calc
                                   metrics.py (pure functions, null-safe)
```

## Under the Hood

- **Q4 recovery.** Companies file no standalone fourth-quarter report — Q4 only
  exists inside the annual 10-K figure. The pipeline detects annual entries by
  duration (off-calendar fiscal years carry no calendar frames) and derives
  Q4 = FY − (Q1+Q2+Q3), strictly: three real quarters or nothing. No estimation.
- **Tag fallbacks across accounting eras.** Revenue lives under different GAAP
  tags before and after ASC 606 (~2018). Each metric maps to an ordered
  candidate list; the modern tag wins where present, legacy tags fill history.
- **Honest nulls everywhere.** Missing data is `None`, never zero and never
  annualized. A ratio without a denominator is unknown, not 0% — and TTM with
  a missing quarter returns nothing rather than an understated sum.
- **Idempotent ingestion.** Re-running writes zero duplicates, enforced both in
  code and by a composite unique constraint on (company, period).

## Setup

Requires Python 3.12+ and PostgreSQL 16.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb moat
cp .env.example .env          # then edit with your DB credentials
alembic upgrade head
python ingest.py MSFT         # or any ticker
uvicorn main:app --reload     # then open http://127.0.0.1:8000/docs
```

Tests: `pytest` (isolated test database — dev data is never touched).

## Tech

Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy 2 (ORM) · Alembic
(migrations) · pytest · GitHub Actions CI

## Scope & limitations

- **Non-financial companies.** Banks and insurers report under a different GAAP
  taxonomy (interest income vs. revenue, deposits vs. long-term debt). Rather
  than sum mismatched tags into misleading ratios, the pipeline reports honest
  gaps for those companies. Sector-specific tag maps are a possible extension.
- **ROIC is simplified.** It uses TTM net income over gross debt + equity;
  proper ROIC uses NOPAT and nets out excess cash. Directionally right,
  documented in the code, refinable.
- **Fundamentals, not real-time.** Data updates when companies file (quarterly),
  not tick by tick — which is the point for value analysis.


## Roadmap
 
- [x] EDGAR ingestion with ticker→CIK lookup (any US-listed company)
- [x] Q4 derivation from annual filings
- [x] Value metrics: margins, ROE, D/E, TTM aggregates, ROIC
- [x] Upsert-based ingestion (field-level refresh without re-ingesting)
- [x] Docker + docker-compose
- [ ] On-demand ingestion (unknown ticker fetched at request time)
- [ ] ML ranking layer across a company universe
- [ ] RAG-powered qualitative briefs from 10-K text