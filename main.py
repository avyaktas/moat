# owns endpoints

import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

import requests
from anthropic import APIError
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

import pipeline
import ratelimit
import timing
from analysis import answer_question
from config import settings
from database import get_db
from filings import find_latest_10k, get_risk_factors
from ingest import fetch_financials, get_cik, ingest_company, store_financials
from logging_config import configure_logging
from metrics import debt_to_equity, fcf_margin, net_margin, roe, roic, ttm
from models import Brief, Company, Financials, Report
from prices import get_price
from report import (
    SynthesisError,
    SynthesisTruncated,
    build_report_data,
    synthesize,
)
from schemas import (
    BriefOut,
    CompanyOut,
    FinancialsOut,
    MetricsOut,
    ReportOut,
)
from serialization import to_jsonable
from views import (
    render_failure,
    render_landing,
    render_not_found,
    render_report,
    render_report_fragment,
    render_report_shell,
)

configure_logging(settings.log_level)
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


# Configuration problems worth knowing about before the first request rather
# than during it. Both are warnings rather than hard failures: the app is
# genuinely useful without an API key - /companies, /financials and /metrics
# need only the database - and refusing to boot would break local development
# and the test suite for no benefit.
if not settings.anthropic_key:
    logger.warning(
        "ANTHROPIC_API_KEY is not set: /brief and /report will fail on any "
        "cache miss. Every other endpoint works."
    )
elif not settings.refresh_token.strip():
    # An exposed deployment with no refresh token is the case worth warning
    # about: anyone can force unlimited paid regeneration.
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
    """Return the company, ingesting it on first request.
    Read through cache: known tickers are served from Postgres, 
    unknown ones trigger a live EDGAR fetch, after which they are cached.
    Tickers the SEC has never heard of still 404."""
    ticker = ticker.upper()
    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is not None:
        return company
    logger.info("ingesting %s: not seen before", ticker)
    try:
        ingest_company(ticker)
    except ValueError:
        # The SEC's ticker file does not list it. That is a real 404: no
        # amount of retrying will produce this company. `from None` because
        # this is expected control flow, not an error worth chaining a
        # traceback onto.
        raise HTTPException(
            status_code=404, detail=f"Unknown ticker: {ticker}"
        ) from None
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

@app.get("/companies", response_model=list[CompanyOut])
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

@app.get("/company/{ticker}", response_model=CompanyOut)
def get_ticker(ticker: TickerPath, db: Session = Depends(get_db)):
    company = get_or_ingest_company(ticker, db)
    return {"id": company.id, "ticker": company.ticker, "name": company.name, "sector": company.sector}

@app.get("/company/{ticker}/financials", response_model=list[FinancialsOut])
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

@app.get("/company/{ticker}/metrics", response_model=MetricsOut)
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


@app.get("/company/{ticker}/brief", response_model=BriefOut)
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
        logger.info("brief cache hit for %s", company.ticker)
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

    logger.info("brief cache miss for %s: analysing filing %s",
                company.ticker, filing["report_date"])
    result = answer_question(question, filing["text"])
    logger.info("brief for %s: addressed=%s grounding=%s",
                company.ticker, result["addressed"], result["grounding_rate"])

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

    A naive value is assumed to be UTC. That assumption is now defensive
    rather than load-bearing: briefs.created_at was the only naive datetime
    column and is timezone-aware as of revision 46498d1ab364, so nothing in
    the schema reaches this branch. Worth being explicit that the assumption
    was not true of that column before the migration - a naive column
    defaulting to now() stores the DATABASE SESSION's local time, not UTC,
    which is exactly why it was changed.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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

# Statuses where the request itself is the problem. Re-sending it unchanged
# produces the same answer, so a retry buys nothing and costs a full synthesis
# call - measured at ~32 seconds - plus the backoff. 400 is the one that
# actually bit: an exhausted usage limit arrives as BadRequestError, which is
# an APIError, so the old blanket handler dutifully retried it.
NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 422})


def _is_retryable(exc: Exception) -> bool:
    """Whether repeating an identical synthesis call could plausibly work.

    Retryable: rate limits (429), server errors and overload (5xx/529),
    connection failures and timeouts, and a reply that would not parse - a
    different sample may well parse.

    Not retryable: the fixed-status client errors above, and a truncated
    response. Truncation is the interesting case because it looks transient
    and is not: the prompt and the token ceiling are unchanged, so the second
    attempt is cut off at the same place. Observed on NVDA, where both
    attempts failed identically and the report degraded after two full calls.
    """
    if isinstance(exc, SynthesisTruncated):
        return False
    if isinstance(exc, SynthesisError):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        # Connection errors and timeouts carry no status. They are exactly
        # what a retry is for.
        return True
    return status not in NON_RETRYABLE_STATUS
@app.get("/company/{ticker}/report", response_model=ReportOut)
def get_report(request: Request, response: Response, ticker: TickerPath,
               refresh: bool = False, token: str | None = None,
               db: Session = Depends(get_db)):
    _enforce_rate_limit(request)
    refresh = _refresh_requested(refresh, token)

    # Look the company up without ingesting. A ticker we have never seen has
    # no cached report by definition, so there is nothing to check and no
    # reason to pay for ingestion before finding that out - and when it is
    # ingested, the fetch happens alongside the filing and price fetches
    # rather than ahead of them.
    ticker = ticker.upper()
    company = db.query(Company).filter(Company.ticker == ticker).first()

    cached = (
        db.query(Report).filter(Report.company_id == company.id).first()
        if company is not None
        else None
    )
    if cached is not None and not refresh:
        generated_at = _as_utc(cached.generated_at)
        age = datetime.now(UTC) - generated_at
        if age < REPORT_MAX_AGE:
            etag = _report_etag(company.ticker, generated_at)

            # Answer a conditional request before touching the payload: no
            # JSON parse, no body, no bytes on the wire.
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=_cache_headers(etag))

            logger.info("report cache hit for %s, age %.1f days",
                        company.ticker, age.total_seconds() / 86400)
            payload = json.loads(cached.payload)
            payload["cache"] = {
                "cached": True,
                "generated_at": generated_at.isoformat(),
                "age_days": round(age.total_seconds() / 86400, 1),
            }
            response.headers.update(_cache_headers(etag))
            return payload

    # cache miss or stale: build it
    logger.info("report cache miss for %s (refresh=%s): rebuilding",
                ticker, refresh)
    if timing.current() is None:
        # No enclosing breakdown (the JSON endpoint called directly). Open one
        # so an uncached build always reports where its time went.
        with timing.track(f"report {ticker}"):
            return _build_report(request, response, ticker, company, refresh, db)
    return _build_report(request, response, ticker, company, refresh, db)


def _prefetch_for_report(ticker: str, need_financials: bool) -> pipeline.Prefetched:
    """Run the report's upstream fetches concurrently, mapping failures.

    The callables are passed as lambdas rather than as direct references so
    each name resolves from this module's globals at call time, which is what
    keeps them monkeypatchable in the tests.
    """
    try:
        return pipeline.prefetch(
            ticker,
            need_financials=need_financials,
            fetch_financials=lambda t: fetch_financials(t),
            fetch_filing=lambda t: _fetch_filing(t),
            fetch_price=lambda t: get_price(t),
        )
    except ValueError:
        # The SEC's ticker file does not list it - a real 404, as in
        # get_or_ingest_company. `from None` because this is expected control
        # flow rather than an error worth a traceback.
        raise HTTPException(
            status_code=404, detail=f"Unknown ticker: {ticker}"
        ) from None
    except requests.RequestException as exc:
        logger.warning("SEC unavailable while ingesting %s: %s", ticker, exc)
        raise HTTPException(
            status_code=502,
            detail="SEC EDGAR is unavailable right now; please try again shortly.",
        ) from exc


def _report_events(response: Response, ticker: str, company: Company | None,
                   refresh: bool, db: Session):
    """Build a report, yielding progress as each stage actually completes.

    A generator rather than a function because two callers want different
    things: the JSON endpoint wants only the finished payload, while the
    streaming endpoint wants to report each stage as it lands and to show the
    computed figures before the model has written anything. Implementing that
    twice would guarantee the two drift; this way there is one build and two
    consumers of it.

    HTTPException still propagates. The JSON endpoint lets FastAPI handle it;
    the stream catches it and turns it into an error event.
    """
    started = time.perf_counter()

    # Everything upstream at once: the financials fetch (only when this ticker
    # has never been seen), the 10-K, and the price. None depends on another's
    # answer, so in series they cost the sum and together they cost the
    # slowest.
    yield pipeline.Stage("fetch", pipeline.STAGE_LABELS["fetch"], "running")
    mark = time.perf_counter()
    fetched = _prefetch_for_report(ticker, need_financials=company is None)
    yield pipeline.Stage("fetch", pipeline.STAGE_LABELS["fetch"], "done",
                         seconds=time.perf_counter() - mark)

    if fetched.series is not None:
        logger.info("ingesting %s: not seen before", ticker)
        yield pipeline.Stage("store", pipeline.STAGE_LABELS["store"], "running")
        mark = time.perf_counter()
        store_financials(ticker, fetched.name, fetched.series)
        # store_financials commits through its own session. This one has been
        # reading since before that commit, so its transaction holds an older
        # snapshot; rolling back ends it and forces the re-read below to open
        # a fresh one. Nothing has been written through this session yet, so
        # there is nothing to lose.
        db.rollback()
        company = db.query(Company).filter(Company.ticker == ticker).first()
        if company is None:
            raise HTTPException(status_code=502, detail="Ingestion failed")
        yield pipeline.Stage("store", pipeline.STAGE_LABELS["store"], "done",
                             seconds=time.perf_counter() - mark)
    else:
        yield pipeline.Stage("store", pipeline.STAGE_LABELS["store"], "skipped")

    yield pipeline.Stage("metrics", pipeline.STAGE_LABELS["metrics"], "running")
    mark = time.perf_counter()

    # Newest first, then limited - so this takes the most recent quarters,
    # which is the order build_report_data documents that it needs.
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .limit(REPORT_QUARTERS)
        .all()
    )

    with timing.stage("metrics"):
        data = build_report_data(rows, fetched.price)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    yield pipeline.Stage("metrics", pipeline.STAGE_LABELS["metrics"], "done",
                         seconds=time.perf_counter() - mark)

    filing = fetched.filing

    def _payload(narrative):
        return {
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

    # Everything above is computed from filed data and is final. Hand it over
    # now so a reader has the scorecard and the figures while the model works.
    partial = _payload(None)
    partial["cache"] = {"cached": False, "generated_at": datetime.now(UTC).isoformat()}
    yield pipeline.Partial(partial)

    narrative = None
    if filing:
        yield pipeline.Stage("synthesis", pipeline.STAGE_LABELS["synthesis"], "running")
        mark = time.perf_counter()
        failure_detail = None
        for attempt in range(SYNTHESIS_ATTEMPTS):
            try:
                with timing.stage("synthesis"):
                    narrative = synthesize(data, filing["text"], company.name)
                break
            except (APIError, SynthesisError) as exc:
                # APIError, not APIStatusError. APIConnectionError and
                # APITimeoutError descend from APIError WITHOUT passing
                # through APIStatusError, so catching the narrower class let
                # an ordinary network blip 500 the whole report instead of
                # degrading it to computed-figures-only.
                retryable = _is_retryable(exc)
                last = attempt == SYNTHESIS_ATTEMPTS - 1
                failure_detail = _synthesis_failure_detail(exc)
                logger.warning(
                    "synthesis attempt %d/%d failed for %s: %s: %s (retryable=%s)",
                    attempt + 1, SYNTHESIS_ATTEMPTS, company.ticker,
                    type(exc).__name__, exc, retryable,
                )
                if not retryable:
                    # Giving up now saves a second full-length call that would
                    # fail the same way, and the sleep before it.
                    break
                if not last:
                    # Back off before retrying; never sleep after the final
                    # attempt, which would delay the response for nothing.
                    time.sleep(SYNTHESIS_BACKOFF_SECONDS * (2 ** attempt))
                # final failure: narrative stays None and the report degrades
        yield pipeline.Stage(
            "synthesis", pipeline.STAGE_LABELS["synthesis"],
            "done" if narrative is not None else "failed",
            seconds=time.perf_counter() - mark,
            detail=None if narrative is not None else failure_detail,
        )
    else:
        yield pipeline.Stage(
            "synthesis", pipeline.STAGE_LABELS["synthesis"], "failed",
            detail="No 10-K filing was available for this company.",
        )

    payload = _payload(narrative)

    # Only persist a complete report. A narrative of None means synthesis
    # failed or the filing was missing; caching that would freeze a degraded
    # NO VERDICT payload for the full 7-day TTL (this happened to IBM on 7/29).
    # Return the degraded payload so the caller still sees the computed data,
    # but skip the write so the next request retries the narrative.
    now = datetime.now(UTC)
    if narrative is not None:
        logger.info("report for %s: verdict=%s grounding=%s",
                    company.ticker, narrative.get("verdict"),
                    narrative.get("grounding_rate"))
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
        with timing.stage("db.report_write"):
            db.execute(stmt)
            db.commit()

    payload["cache"] = {"cached": False, "generated_at": now.isoformat()}
    if narrative is not None:
        response.headers.update(_cache_headers(_report_etag(company.ticker, now)))
    else:
        # A degraded report was deliberately not persisted so the next request
        # retries. Letting a client cache it would defeat exactly that.
        response.headers["Cache-Control"] = "no-store"

    logger.info("report %s built in %.2fs", ticker, time.perf_counter() - started)
    yield pipeline.Result(payload)


def _synthesis_failure_detail(exc: Exception) -> str:
    """A sentence a reader can act on, rather than an exception repr."""
    from report import SynthesisTruncated

    if isinstance(exc, SynthesisTruncated):
        return "The analysis ran past its length limit and could not be completed."
    status = getattr(exc, "status_code", None)
    if status == 429:
        return "The analysis service is rate limited right now."
    if status in (401, 403):
        return "The analysis service rejected our credentials."
    if status == 400:
        return "The analysis service refused the request; its usage limit may be reached."
    if status is not None and status >= 500:
        return "The analysis service is unavailable right now."
    if isinstance(exc, SynthesisError):
        return "The model's response could not be read."
    return "The analysis could not be generated."


def _build_report(request: Request, response: Response, ticker: str,
                  company: Company | None, refresh: bool, db: Session):
    """Drive the build to completion and return the payload.

    The JSON endpoint wants only the end of the sequence; the progress events
    exist for the streaming endpoint.
    """
    payload = None
    for event in _report_events(response, ticker, company, refresh, db):
        if isinstance(event, pipeline.Result):
            payload = event.payload
    return payload


def _sse(event: str, payload: dict) -> str:
    """One server-sent event.

    The data is JSON on a single line. SSE frames are line-delimited, so an
    HTML fragment containing newlines cannot be written raw - JSON escapes
    them, which is why fragments travel as a field rather than as the body.
    """
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@app.get("/company/{ticker}/report/stream")
def stream_report(request: Request, ticker: TickerPath, refresh: bool = False,
                  token: str | None = None, db: Session = Depends(get_db)):
    """Build a report, reporting each stage as it actually completes.

    The page opens this as an EventSource. It emits `stage` as each step
    lands, `partial` once the computed figures exist - around two seconds,
    against the thirty the narrative takes - `done` with the finished sheet,
    and `failed` with something a person can read.

    `done` carries the rendered HTML rather than telling the page to reload.
    A degraded report is deliberately not cached, so a reload would re-run the
    entire pipeline, including the model call that just failed.
    """
    _enforce_rate_limit(request)
    resolved_refresh = _refresh_requested(refresh, token)
    normalized = ticker.upper()

    def events():
        # A plain Response collects the cache headers the build sets; they are
        # not used on the stream itself, which must never be cached.
        sink = Response()
        try:
            # No timing.track here. Starlette drives this generator through a
            # thread pool and each resumption gets a fresh copy of the
            # context, so a breakdown opened around the yields would never
            # survive to the next step. The per-stage lines still log; the
            # aggregate breakdown belongs to the JSON path, which runs
            # straight through.
            company = (
                db.query(Company).filter(Company.ticker == normalized).first()
            )
            for event in _report_events(
                sink, normalized, company, resolved_refresh, db
            ):
                if isinstance(event, pipeline.Stage):
                    yield _sse("stage", event.as_dict())
                elif isinstance(event, pipeline.Partial):
                    yield _sse("partial", {
                        "html": render_report_fragment(
                            event.payload, pending=True
                        ),
                    })
                elif isinstance(event, pipeline.Result):
                    yield _sse("done", {
                        "html": render_report_fragment(event.payload),
                    })
        except HTTPException as exc:
            yield _sse("failed", {
                "status": exc.status_code,
                "html": render_failure(_failure_title(exc.status_code),
                                       str(exc.detail)),
            })
        except Exception:
            # Nothing may escape a streaming response: once the body has
            # begun, an unhandled exception truncates it and the page waits
            # forever on an event that will never arrive.
            logger.exception("report stream failed for %s", normalized)
            yield _sse("failed", {
                "status": 500,
                "html": render_failure(
                    "Something went wrong",
                    "The report could not be generated. This has been logged.",
                ),
            })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Tell nginx-style proxies not to buffer, which would hold every
            # event until the response completed and defeat the entire point.
            "X-Accel-Buffering": "no",
        },
    )


def _failure_title(status: int) -> str:
    if status == 404:
        return "Not found"
    if status == 502:
        return "Upstream unavailable"
    if status == 429:
        return "Too many requests"
    if status == 403:
        return "Not permitted"
    return "Could not build this report"


@app.get("/company/{ticker}/report/view", response_class=HTMLResponse)
def get_report_view(request: Request, response: Response, ticker: TickerPath,
                    refresh: bool = False, token: str | None = None,
                    db: Session = Depends(get_db)):
    """The tearsheet: served whole when cached, built live when not.

    A fresh cached report renders immediately, conditional requests and all -
    that path is unchanged and costs nothing.

    Otherwise the report has to be built, which takes about thirty seconds,
    almost all of it the model writing. Rather than hold the connection open
    with nothing on screen, this returns the shell at once and the page builds
    itself from the stream.

    The ticker is still resolved before the shell is returned. An unknown one
    gets the same HTML 404 as before rather than a loading screen that fails a
    moment later - get_cik is cached in-process, so this costs nothing after
    the first request.
    """
    normalized = ticker.upper()
    company = db.query(Company).filter(Company.ticker == normalized).first()
    if company is not None and not refresh:
        cached = (
            db.query(Report).filter(Report.company_id == company.id).first()
        )
        if cached is not None:
            age = datetime.now(UTC) - _as_utc(cached.generated_at)
            if age < REPORT_MAX_AGE:
                report = get_report(request, response, ticker, refresh=refresh,
                                    token=token, db=db)
                # A conditional request short-circuits to 304 before a payload
                # exists; pass that straight through rather than rendering it.
                if isinstance(report, Response):
                    return report
                return HTMLResponse(render_report(report),
                                    headers=dict(response.headers))

    try:
        get_cik(normalized)
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Unknown ticker: {normalized}"
        ) from None
    except requests.RequestException as exc:
        logger.warning("SEC unavailable during CIK lookup for %s: %s",
                       normalized, exc)
        raise HTTPException(
            status_code=502,
            detail="SEC EDGAR is unavailable right now; please try again shortly.",
        ) from exc

    return HTMLResponse(
        render_report_shell(normalized),
        headers={"Cache-Control": "no-store"},
    )