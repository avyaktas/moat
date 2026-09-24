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
from report import SynthesisError, build_report_data, synthesize
from datetime import datetime, timedelta, timezone
from views import render_report, render_landing, render_not_found
import time
import requests
from anthropic import APIError

from sqlalchemy.dialects.postgresql import insert as pg_insert
import json
import logging

from analysis import answer_question
from filings import find_latest_10k, get_risk_factors

from ingest import get_cik

from serialization import to_jsonable


logger = logging.getLogger(__name__)

app = FastAPI()

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
        # The SEC's ticker file does not list it. That is a real 404: no
        # amount of retrying will produce this company.
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except requests.RequestException as exc:
        # The SEC was unreachable, slow, or throttling - it rate-limits at
        # 10 req/s, so a 429 is an ordinary event rather than an exception.
        # Every SEC call ends in raise_for_status(), and catching only
        # ValueError let those escape as a 500 with a stack trace, which
        # blames this application for the upstream being busy.
        logger.warning("SEC unavailable while ingesting %s: %s", ticker, exc)
        raise HTTPException(
            status_code=502,
            detail="SEC EDGAR is unavailable right now; please try again shortly.",
        ) from exc

    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is None:
        raise HTTPException(status_code=502, detail="Ingestion failed")
    return company


def _fetch_filing(ticker: str) -> dict | None:
    """Fetch a company's latest Risk Factors, or None if the SEC would not say.

    Returning None rather than raising is what lets /report degrade instead of
    dying: the computed figures come from already-stored financials and do not
    need the filing, so an EDGAR outage should cost the narrative and nothing
    else. /brief takes the opposite view and raises, because there the filing
    IS the product.
    """
    try:
        cik, _ = get_cik(ticker)
        return get_risk_factors(cik)
    except requests.RequestException as exc:
        logger.warning("SEC unavailable while fetching filing for %s: %s", ticker, exc)
        return None
    


def _is_html_route(path: str) -> bool:
    """True for the routes a person browses, as opposed to calls an API makes.

    Kept as a predicate rather than inlined so the rule has one definition:
    any future HTML route is added here, and the 404 follows automatically.
    """
    return path.rstrip("/").endswith("/view")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Answer a 404 in the format the route itself speaks.

    A human who mistyped a ticker into the search box should land on the
    on-brand page with a way back, not raw JSON. But the branch that decided
    this keyed on the /company/ path prefix, which every JSON endpoint also
    shares - so GET /company/FAKE/report, an API call, came back as markup.

    The view routes are the HTML ones. Everything else stays JSON, because an
    API that changes content type on the error path is not an API.
    """
    if exc.status_code == 404 and _is_html_route(request.url.path):
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


def _brief_is_current(ticker: str, cached: Brief) -> bool:
    """True if a cached brief was written against the company's newest 10-K.

    Briefs had no expiry and no refresh, so one generated against last year's
    filing was served indefinitely - while the row already stored the
    report_date that would have revealed it. Comparing against the newest
    filing is both more correct and cheaper than a blind TTL: it re-reads the
    submissions index, a small JSON, rather than the 8MB document, and it
    regenerates exactly when the answer could actually have changed.

    If the SEC cannot be reached, the cached brief is treated as current. A
    possibly-stale answer beats no answer, and refusing to serve a page we
    already have because an upstream is down would be the wrong trade.
    """
    try:
        cik, _ = get_cik(ticker)
        latest = find_latest_10k(cik)
    except requests.RequestException as exc:
        logger.warning("could not check filing freshness for %s: %s", ticker, exc)
        return True

    if latest is None:
        return True
    return cached.report_date == latest["report_date"]


@app.get("/company/{ticker}/brief")
def get_brief(ticker: str, question: str = DEFAULT_QUESTION,
              refresh: bool = False, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)

    # Cache check first: on a hit the only upstream work is a freshness
    # probe, which find_latest_10k serves from its own cache most of the time.
    cached = (
        db.query(Brief)
        .filter(Brief.company_id == company.id, Brief.question == question)
        .first()
    )
    if cached is not None and not refresh and _brief_is_current(company.ticker, cached):
        return _brief_to_dict(cached)

    try:
        cik, _ = get_cik(company.ticker)
    except requests.RequestException as exc:
        logger.warning("SEC unavailable during CIK lookup for %s: %s",
                       company.ticker, exc)
        raise HTTPException(
            status_code=502,
            detail="SEC EDGAR is unavailable right now; please try again shortly.",
        ) from exc

    # cache miss, stale, or refresh: fetch filing, run analysis
    try:
        filing = get_risk_factors(cik)
    except requests.RequestException as exc:
        logger.warning("SEC unavailable while fetching filing for %s: %s",
                       company.ticker, exc)
        raise HTTPException(
            status_code=502,
            detail="SEC EDGAR is unavailable right now; please try again shortly.",
        ) from exc

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


REPORT_MAX_AGE = timedelta(days=7)

# One try plus one retry. A second failure means the outage is not a blip,
# and a caller waiting on a report would rather have the computed figures now
# than a third attempt's latency.
SYNTHESIS_ATTEMPTS = 2
SYNTHESIS_BACKOFF_SECONDS = 3
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
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .all()
    )

    data = build_report_data(rows, get_price(company.ticker))
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    filing = _fetch_filing(company.ticker)
    narrative = None
    if filing:
        for attempt in range(SYNTHESIS_ATTEMPTS):
            try:
                narrative = synthesize(data, filing["text"], company.name)
                break
            except (APIError, SynthesisError) as exc:
                # APIError, not APIStatusError. APIConnectionError and
                # APITimeoutError descend from APIError WITHOUT passing
                # through APIStatusError, so catching the narrower class let
                # an ordinary network blip 500 the whole report instead of
                # degrading it to computed-figures-only.
                last = attempt == SYNTHESIS_ATTEMPTS - 1
                logger.warning(
                    "synthesis attempt %d/%d failed for %s: %s: %s",
                    attempt + 1, SYNTHESIS_ATTEMPTS, company.ticker,
                    type(exc).__name__, exc,
                )
                if not last:
                    # Back off before retrying; never sleep after the final
                    # attempt, which would delay the response for nothing.
                    time.sleep(SYNTHESIS_BACKOFF_SECONDS * (2 ** attempt))
                # final failure: narrative stays None and the report degrades

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