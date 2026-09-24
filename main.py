# owns endpoints

from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, Path, Query, Request
from fastapi import Response
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from models import Company, Financials, Brief, Report
from database import get_db
from metrics import debt_to_equity, fcf_margin, net_margin, roe, ttm, roic
from ingest import ingest_company
from prices import get_price
from report import SynthesisError, build_report_data, synthesize
from datetime import datetime, timedelta, timezone
from views import render_report, render_landing, render_not_found
import secrets
import time
import requests
from anthropic import APIError

import ratelimit
from config import settings

from sqlalchemy.dialects.postgresql import insert as pg_insert
import hashlib
import json
import logging

from analysis import answer_question
from filings import find_latest_10k, get_risk_factors

from ingest import get_cik

from serialization import to_jsonable


logger = logging.getLogger(__name__)

app = FastAPI()

# Real tickers are short and alphanumeric, allowing the dot and dash that
# appear in class shares (BRK.B, BF-B). Bounding the shape here rejects junk
# at the edge, before it costs a database round trip or an SEC lookup, and
# keeps unbounded user input out of the path entirely.
def _client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Railway terminates TLS and proxies, so request.client.host is the proxy
    and X-Forwarded-For carries the original client. The leftmost entry is
    the one the edge saw. A caller can forge that header, which is another
    reason this is a speed bump rather than a security control - see the
    module docstring in ratelimit.py.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(request: Request) -> None:
    """Reject a caller who is spending faster than the configured rate."""
    key = _client_key(request)
    if ratelimit.allow(key, settings.rate_limit_burst,
                       settings.rate_limit_per_minute):
        return
    retry = ratelimit.retry_after_seconds(settings.rate_limit_per_minute)
    logger.warning("rate limit hit by %s on %s", key, request.url.path)
    raise HTTPException(
        status_code=429,
        detail="Too many requests. This endpoint generates a paid analysis; "
               "please slow down.",
        headers={"Retry-After": str(retry)},
    )


def _refresh_requested(refresh: bool, token: str | None) -> bool:
    """Whether to honour ?refresh=, which forces a paid regeneration.

    With no refresh_token configured this is permitted, which keeps local
    development and the test suite working unchanged. With one configured it
    must match, compared in constant time so the check does not leak the
    secret through timing.
    """
    if not refresh:
        return False

    configured = settings.refresh_token.strip()
    if not configured:
        return True

    if not token or not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=403,
            detail="refresh requires a valid token",
        )
    return True


# An exposed deployment with no refresh token is the case worth warning about:
# anyone can force unlimited paid regeneration. Logged once at import rather
# than per request.
if settings.anthropic_key and not settings.refresh_token.strip():
    logger.warning(
        "refresh_token is not set: ?refresh= can force paid regeneration "
        "without a credential"
    )


TickerPath = Annotated[
    str, Path(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9.\-]+$")
]

DEFAULT_COMPANIES_PAGE = 100
MAX_COMPANIES_PAGE = 500

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
    # getattr because StarletteHTTPException carries headers but the bare
    # 404s Starlette raises for unmatched routes do not. Dropping them silently
    # cost the Retry-After on a 429, which is the one header a rate-limited
    # caller actually needs.
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.get("/", response_class=HTMLResponse)
def read_root():
    return render_landing()

@app.get("/health")
def read_health(db: Session = Depends(get_db)):
    """Report whether this instance can actually serve a request.

    It used to return {"status": "ok"} without touching anything, which is the
    one thing a health check must not do: every endpoint here needs Postgres,
    so a container healthcheck wired to this would have kept an instance in
    rotation while every real request failed on a dead connection pool.

    The success shape is unchanged, so existing callers and the existing test
    see exactly what they saw before.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("health check failed: database unreachable: %s", exc)
        return JSONResponse(
            {"status": "degraded", "database": "unreachable"},
            status_code=503,
        )
    return {"status": "ok"}

@app.get("/companies")
def list_companies(
    limit: Annotated[int, Query(ge=1, le=MAX_COMPANIES_PAGE)] = DEFAULT_COMPANIES_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    """List known companies, newest-registered last.

    Ordered by id and paginated. An unbounded list endpoint is a slow query
    waiting for the table to grow, and paginating an unordered query can
    repeat or skip rows between pages, since Postgres is under no obligation
    to return them in the same order twice.
    """
    rows = (
        db.query(Company)
        .order_by(Company.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [
        {"id": r.id, "ticker": r.ticker, "name": r.name, "sector": r.sector}
        for r in rows
    ]

@app.get("/company/{ticker}")
def get_ticker(ticker: TickerPath, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)
    return {"id": company.id, "ticker": company.ticker, "name": company.name, "sector": company.sector}

@app.get("/company/{ticker}/financials")
def get_financials(ticker: TickerPath, db: Session = Depends(get_db)):
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
def get_metrics(ticker: TickerPath, db: Session = Depends(get_db)):
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

# Long enough for any real question, and far below the ~2704-byte ceiling
# Postgres puts on a btree entry. question is part of uq_company_question, and
# an unbounded value that does not compress raises
#   index row size 3016 exceeds btree version 4 maximum 2704
# as an unhandled 500 - reachable by anyone, with a long enough query string.
MAX_QUESTION_LENGTH = 500

# build_report_data reads at most rows[:20]: four quarters for TTM, four more
# for the prior-year comparison, and twenty for the margin-stability history.
# Loading every quarter a company ever filed to use the newest twenty is work
# that grows forever while the answer never changes.
REPORT_QUARTERS = 20

# How long a client may reuse a report without asking again. Deliberately far
# shorter than the 7-day server-side TTL: the server knows when it rebuilt the
# payload, a browser does not, and an over-long max-age would leave a stale
# report pinned in caches with no way to reach it. Short freshness plus an
# ETag gives the real win anyway - a revalidation costs one 304 with no body,
# no JSON parse, and no database read of the payload column.
REPORT_CLIENT_MAX_AGE = 300

QuestionQuery = Annotated[str, Query(max_length=MAX_QUESTION_LENGTH)]


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
def get_brief(request: Request, ticker: TickerPath,
              question: QuestionQuery = DEFAULT_QUESTION,
              refresh: bool = False, token: str | None = None,
              db: Session = Depends(get_db)):
    _enforce_rate_limit(request)
    refresh = _refresh_requested(refresh, token)
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
        # Brief.created_at is a naive column; _as_utc stamps it so a consumer
        # is not left guessing which zone the value is in.
        "cached_at": _as_utc(b.created_at).isoformat() if b.created_at else None,
    }


REPORT_MAX_AGE = timedelta(days=7)


def _as_utc(value: datetime) -> datetime:
    """Return an aware datetime expressed in UTC.

    Postgres hands back timestamptz in the SESSION timezone, not in UTC - the
    same instant written as 18:44:35+00:00 comes back as 14:44:35-04:00 on a
    connection whose timezone is America/New_York. The instant is correct and
    comparisons still work, but isoformat() produces a different string, and
    two things depended on that string: the ETag, which then changed on every
    request and could never produce a 304, and the tearsheet footer, which
    renders value[:19] and appends " UTC" - displaying local time under a
    label asserting it was not.

    A naive value is assumed to be UTC, which is what the column stores.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _report_etag(ticker: str, generated_at: datetime) -> str:
    """A validator for one company's report as generated at one instant.

    Derived from the generation timestamp rather than from a hash of the body,
    so it can be computed from the cache row alone - before the payload column
    is parsed. That is what lets a conditional request be answered without
    deserializing the report at all.
    """
    raw = f"{ticker}|{_as_utc(generated_at).isoformat()}"
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:32] + '"'


def _cache_headers(etag: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": f"public, max-age={REPORT_CLIENT_MAX_AGE}, must-revalidate",
    }

# One try plus one retry. A second failure means the outage is not a blip,
# and a caller waiting on a report would rather have the computed figures now
# than a third attempt's latency.
SYNTHESIS_ATTEMPTS = 2
SYNTHESIS_BACKOFF_SECONDS = 3
@app.get("/company/{ticker}/report")
def get_report(request: Request, response: Response, ticker: TickerPath,
               refresh: bool = False, token: str | None = None,
               db: Session = Depends(get_db)):
    _enforce_rate_limit(request)
    refresh = _refresh_requested(refresh, token)
    company = get_or_ingest_company(ticker, db)

    cached = (
        db.query(Report)
        .filter(Report.company_id == company.id)
        .first()
    )
    if cached is not None and not refresh:
        generated_at = _as_utc(cached.generated_at)
        age = datetime.now(timezone.utc) - generated_at
        if age < REPORT_MAX_AGE:
            etag = _report_etag(company.ticker, generated_at)

            # Answer a conditional request before touching the payload: no
            # JSON parse, no body, no bytes on the wire.
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=_cache_headers(etag))

            payload = json.loads(cached.payload)
            payload["cache"] = {
                "cached": True,
                "generated_at": generated_at.isoformat(),
                "age_days": round(age.total_seconds() / 86400, 1),
            }
            response.headers.update(_cache_headers(etag))
            return payload

    # cache miss or stale: build it
    # Newest first, then limited - so this takes the most recent quarters,
    # which is the order build_report_data documents that it needs.
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .limit(REPORT_QUARTERS)
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
    if narrative is not None:
        response.headers.update(_cache_headers(_report_etag(company.ticker, now)))
    else:
        # A degraded report was deliberately not persisted so the next request
        # retries. Letting a client cache it would defeat exactly that.
        response.headers["Cache-Control"] = "no-store"
    return payload

@app.get("/company/{ticker}/report/view", response_class=HTMLResponse)
def get_report_view(request: Request, response: Response, ticker: TickerPath,
                    refresh: bool = False, token: str | None = None,
                    db: Session = Depends(get_db)):
    """The same report, rendered as a readable tearsheet."""
    report = get_report(request, response, ticker, refresh=refresh,
                        token=token, db=db)
    # A conditional request short-circuits to 304 before a payload exists;
    # pass that straight through rather than trying to render it.
    if isinstance(report, Response):
        return report
    return HTMLResponse(render_report(report), headers=dict(response.headers))