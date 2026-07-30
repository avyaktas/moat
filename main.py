# owns endpoints

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session
from models import Company, Financials, Brief, Report
from database import get_db
from metrics import debt_to_equity, fcf_margin, net_margin, roe, ttm, roic
from ingest import ingest_company
from prices import get_price
from report import build_report_data, synthesize
from datetime import datetime, timedelta, timezone
from views import render_report, render_landing, render_not_found

from sqlalchemy.dialects.postgresql import insert as pg_insert
import json
import logging
import anthropic
from analysis import answer_question
from filings import get_risk_factors

from ingest import get_cik

from decimal import Decimal


logger = logging.getLogger(__name__)

app = FastAPI()

def to_jsonable(v):
    """Cache-write boundary: Decimals become floats, dates become ISO strings.
    Anything else unexpected fails loudly instead of being silently stringified."""
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):  # date, datetime
        return v.isoformat()
    raise TypeError(f"Not JSON serializable: {type(v)}")

def get_or_ingest_company(ticker: str, db: Session) -> Company:
    """Retur the company, ingesting it on first request.
    Read through cache: known tickers are served from Postgres, 
    unkown ones trigger a live EDGAR fetch, after which they've cached. 
    Tickers SEC has never heard of stil 404"""
    ticker = ticker.upper()
    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is not None:
        return company
    try: 
        ingest_company(ticker)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unkown ticker: {ticker}")
    
    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is None:
        raise HTTPException(status_code=502, detail="Ingestion failed")
    return company
    


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """A 404 under /company/* means a human mistyped a ticker or asked for a
    company with no filing. Give them the on-brand page with a way back to
    search, not raw JSON. Every other error keeps the default JSON shape so
    the API stays an API."""
    if exc.status_code == 404 and request.url.path.startswith("/company/"):
        return HTMLResponse(render_not_found(exc.detail), status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse)
def read_root():
    return render_landing()

@app.get("/health")
def read_health():
    return {"status": "ok"}

@app.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = db.query(Company).all()
    return [
        {"id": r.id, "ticker": r.ticker, "name": r.name, "sector": r.sector}
        for r in rows
    ]

@app.get("/company/{ticker}")
def get_ticker(ticker: str, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)
    return {"id": company.id, "ticker": company.ticker, "name": company.name, "sector": company.sector}

@app.get("/company/{ticker}/financials")
def get_financials(ticker: str, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .all()
    )
    return [
    {
        "period_end": r.period_end,
        "revenue": r.revenue,
        "net_income": r.net_income,
        "free_cash_flow": r.free_cash_flow,
        "total_debt": r.total_debt,
        "shareholders_equity": r.shareholders_equity,
    }
    for r in rows
]

@app.get("/company/{ticker}/metrics")
def get_metrics(ticker: str, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .all()
    )
    
    quarterly = [
        {
            "period_end": r.period_end,
            "net_margin": net_margin(r.revenue, r.net_income),
            "fcf_margin": fcf_margin(r.revenue, r.free_cash_flow),
            "roe": roe(r.net_income, r.shareholders_equity),
            "debt_to_equity": debt_to_equity(r.total_debt, r.shareholders_equity),
        }
        for r in rows
    ]
    ttm_income = ttm([r.net_income for r in rows[:4]])
    ttm_revenue = ttm([r.revenue for r in rows[:4]])
    ttm_fcf = ttm([r.free_cash_flow for r in rows[:4]])
    latest = rows[0] if rows else None

    ttm_block = {
        "revenue": ttm_revenue,
        "net_income": ttm_income,
        "free_cash_flow": ttm_fcf,
        "net_margin": net_margin(ttm_revenue, ttm_income),
        "fcf_margin": fcf_margin(ttm_revenue, ttm_fcf),
        "roe": roe(ttm_income, latest.shareholders_equity) if latest else None,
        "roic": roic(ttm_income, latest.total_debt, latest.shareholders_equity) if latest else None,
    }

    return {"quarterly": quarterly, "ttm": ttm_block}


DEFAULT_QUESTION = "What are the most significant risks this company identifies, and how does it describe them?"


@app.get("/company/{ticker}/brief")
def get_brief(ticker: str, question: str = DEFAULT_QUESTION, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)
    cik, _ = get_cik(company.ticker)
    #cache check
    cached = (
        db.query(Brief)
        .filter(Brief.company_id == company.id, Brief.question == question)
        .first()
    )
    if cached is not None:
        return _brief_to_dict(cached)

    # cache miss: fetch filing, run analysis
    filing = get_risk_factors(cik)

    if filing is None:
        raise HTTPException(status_code=404, detail="No 10-K filing found")

    result = answer_question(question, filing["text"])

    values = {
        "company_id": company.id,
        "question": question,
        "answer": result["answer"] or "",
        "addressed": bool(result["addressed"]),
        "quotes": json.dumps(result["quotes"]),
        "grounding_rate": result["grounding_rate"],
        "filing_url": filing["url"],
        "report_date": filing["report_date"],
    }
    stmt = pg_insert(Brief).values(**values).on_conflict_do_update(
        constraint="uq_company_question",
        set_={k: v for k, v in values.items() if k not in ("company_id", "question")},
    )
    db.execute(stmt)
    db.commit()

    brief = (
        db.query(Brief)
        .filter(Brief.company_id == company.id, Brief.question == question)
        .first()
    )
    return _brief_to_dict(brief)

def _brief_to_dict(b: Brief) -> dict:
    return {
        "question": b.question,
        "addressed": b.addressed,
        "answer": b.answer,
        "quotes": json.loads(b.quotes),
        "grounding_rate": b.grounding_rate,
        "filing_url": b.filing_url,
        "report_date": b.report_date,
        "cached_at": b.created_at.isoformat() if b.created_at else None,
    }


REPORT_MAX_AGE = timedelta(days=7
                           )
@app.get("/company/{ticker}/report")
def get_report(ticker: str, refresh: bool = False, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)

    cached = (
        db.query(Report)
        .filter(Report.company_id == company.id)
        .first()
    )
    if cached is not None and not refresh:
        age = datetime.now(timezone.utc) - cached.generated_at
        if age < REPORT_MAX_AGE:
            payload = json.loads(cached.payload)
            payload["cache"] = {
                "cached": True,
                "generated_at": cached.generated_at.isoformat(),
                "age_days": round(age.total_seconds() / 86400, 1),
            }
            return payload

    # cache miss or stale: build it
    cik, _ = get_cik(company.ticker)
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .all()
    )

    data = build_report_data(rows, get_price(company.ticker))
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    filing = get_risk_factors(cik)
    narrative = None
    if filing:
        try:
            narrative = synthesize(data, filing["text"], company.name)
        except anthropic.APIStatusError as e:
            # An Anthropic outage (a 529 overload took the whole endpoint
            # down this week) must degrade the report, not kill it. Leave
            # narrative None; Task 1's guard then keeps the degraded payload
            # out of the cache so the next request retries.
            logger.warning(
                "synthesize failed for %s (%s); serving degraded report",
                company.ticker, e,
            )
            narrative = None

    payload = {
        "company": company.ticker,
        "name": company.name,
        "data": data,
        "narrative": narrative,
        "sources": {
            "financials": "SEC EDGAR XBRL companyfacts",
            "filing": filing["url"] if filing else None,
            "report_date": filing["report_date"] if filing else None,
            "price": "yfinance (market data; not from filings)",
        },
    }

    # Only persist a complete report. A narrative of None means synthesis
    # failed or the filing was missing; caching that would freeze a degraded
    # NO VERDICT payload for the full 7-day TTL (this happened to IBM on 7/29).
    # Return the degraded payload so the caller still sees the computed data,
    # but skip the write so the next request retries the narrative.
    now = datetime.now(timezone.utc)
    if narrative is not None:
        payload_json = json.dumps(payload, default=to_jsonable)
        stmt = pg_insert(Report).values(
            company_id=company.id,
            payload=payload_json,
            generated_at=now,
        ).on_conflict_do_update(
            constraint="uq_report_company",
            set_={"payload": payload_json,
                  "generated_at": now},
        )
        db.execute(stmt)
        db.commit()

    payload["cache"] = {"cached": False, "generated_at": now.isoformat()}
    return payload

@app.get("/company/{ticker}/report/view", response_class=HTMLResponse)
def get_report_view(ticker: str, refresh: bool = False, db: Session = Depends(get_db)):
    """The same report, rendered as a readable tearsheet."""
    report = get_report(ticker, refresh=refresh, db=db)
    return render_report(report)