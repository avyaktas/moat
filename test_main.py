import json

import pytest

import analysis
from conftest import TestingSessionLocal
from models import Company, Report


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_get_company(client):
    response = client.get("/company/MSFT")
    assert response.status_code == 200
    assert response.json()["ticker"] == "MSFT"

def test_company_lowercase(client):
    response = client.get("/company/msft")
    assert response.status_code == 200
    assert response.json()["ticker"] == "MSFT"

def test_unknown_ticker_404s_without_network(client, monkeypatch):
    """Renamed from test_get_company_not_found, which was defined twice in
    this file - Python bound the second definition and this one never ran.

    It also made a live SEC request, despite the name: with get_cik unmocked,
    /company/FAKE downloads the full ticker file to discover FAKE is not in
    it.
    """
    monkeypatch.setattr("main.ingest_company", _raise_unknown)
    response = client.get("/company/FAKE")
    assert response.status_code == 404

def test_get_financials_empty(client):
    response = client.get("/company/MSFT/financials")
    assert response.status_code == 200
    assert response.json() == []


def _raise_unknown(ticker):
    raise ValueError(f"Unknown ticker: {ticker}")

def test_get_company_not_found(client, monkeypatch):
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    response = client.get("/company/FAKE")
    assert response.status_code == 404

def test_landing_page_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Moat" in resp.text
    # The ticker box sends the user to a report tearsheet.
    assert "/report/view" in resp.text


def test_company_404_returns_html_page(client, monkeypatch):
    # Both bindings: the view route resolves the ticker through main.get_cik
    # before serving a loading shell, so an unknown one still 404s rather than
    # showing a page that fails a moment later.
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    monkeypatch.setattr("main.get_cik", _raise_unknown)
    resp = client.get("/company/FAKE/report/view")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Not found" in resp.text
    assert 'href="/"' in resp.text          # a way back to search


def test_non_company_404_stays_json(client):
    resp = client.get("/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def _fake_answer(question, source_text, client=None):
    """Stand-in for the LLM call — deterministic, no network, no cost."""
    return {
        "addressed": True,
        "answer": "Microsoft identifies competition as a key risk.",
        "quotes": ["We face intense competition."],
        "quote_checks": [True],
        "grounding_rate": 1.0,
        "raw": "{}",
    }


def _fake_risk_factors(cik):
    """Stand-in for the 10-K fetch — no SEC call."""
    return {
        "text": "ITEM 1A. RISK FACTORS We face intense competition.",
        "url": "https://example.com/fake-10k.htm",
        "filing_date": "2025-07-30",
        "report_date": "2025-06-30",
    }


def test_brief_generates_and_caches(client, monkeypatch):
    # Patch BOTH boundaries: the LLM and the SEC fetch.
    monkeypatch.setattr(analysis, "answer_question", _fake_answer)
    monkeypatch.setattr("main.answer_question", _fake_answer)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))

    resp = client.get("/company/MSFT/brief")
    assert resp.status_code == 200
    body = resp.json()
    assert body["addressed"] is True
    assert body["grounding_rate"] == 1.0
    assert "competition" in body["answer"].lower()


def _fake_report_data(rows, price_data):
    """Stand-in for build_report_data - valid computed figures, no DB rows.

    Shaped like the real return value rather than the minimum the assertions
    happen to touch. The response models added in schemas.py rejected the
    earlier stub, correctly: it was missing financial_health and every
    scorecard summary field, so it could never have been a real response. A
    fake that cannot satisfy the contract the endpoint actually returns is a
    fake that can hide a real bug.
    """
    return {
        "as_of": "2025-06-30",
        "ttm": {
            "revenue": 100.0, "net_income": 30.0, "free_cash_flow": 25.0,
            "net_margin": 0.30, "fcf_margin": 0.25, "roe": 0.075,
            "roic": 0.068, "revenue_growth": 0.0,
        },
        "price": None,
        "scorecard": {
            "checks": [],
            "summary": {"passed": 0, "failed": 0, "unknown": 6, "evaluable": 0},
            "financial_health": {"survivability": {"verdict": "Cannot assess"}},
            "valuation": {"market_cap": None, "p_fcf": None, "p_e": None},
        },
    }


def test_report_not_cached_when_narrative_is_none(client, monkeypatch):
    """A failed synthesis (narrative None) must not be cached, but the
    computed data still comes back — degraded, not down."""
    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    # Synthesis fails: return None (Task 1 must then skip the cache write).
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    body = resp.json()

    # Degraded but useful: computed data present, narrative explicitly absent.
    assert body["narrative"] is None
    assert body["data"]["ttm"]["revenue"] == 100.0

    # Nothing was persisted, so the next request will retry synthesis.
    db = TestingSessionLocal()
    try:
        assert db.query(Report).count() == 0
    finally:
        db.close()


def test_report_survives_anthropic_outage(client, monkeypatch):
    """A 529 (or any APIStatusError) from Anthropic must degrade the report,
    not take the endpoint down — and the degraded payload isn't cached."""
    import anthropic
    import httpx

    def _raise_overloaded(*a, **k):
        response = httpx.Response(
            529, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        raise anthropic.APIStatusError("Overloaded", response=response, body=None)

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _raise_overloaded)
    # The retry backoff is asserted in test_no_sleep_after_the_final_attempt;
    # sitting through it here only slows the suite.
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    body = resp.json()

    assert body["narrative"] is None
    assert body["data"]["ttm"]["revenue"] == 100.0

    db = TestingSessionLocal()
    try:
        assert db.query(Report).count() == 0
    finally:
        db.close()

def test_report_not_cached_when_synthesis_returns_bad_json(client, monkeypatch):
    """A model reply that will not parse must degrade, not freeze.

    The previous guard was `if narrative is not None`, and the old failure
    value was a truthy {"error": ...} dict - so an unparseable reply was
    cached and served as NO VERDICT for the full 7-day TTL. This is the IBM
    failure reached by a second route.
    """
    from report import SynthesisError

    def _raise_bad_json(*a, **k):
        raise SynthesisError("Model response was not valid JSON", raw="nonsense")

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _raise_bad_json)
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    body = resp.json()

    assert body["narrative"] is None
    assert body["data"]["ttm"]["revenue"] == 100.0

    db = TestingSessionLocal()
    try:
        assert db.query(Report).count() == 0
    finally:
        db.close()


def _connection_error():
    import anthropic
    import httpx

    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


def test_report_survives_anthropic_connection_error(client, monkeypatch):
    """APIConnectionError is NOT an APIStatusError subclass.

    Its MRO is APIConnectionError -> APIError -> AnthropicError, so the
    original `except APIStatusError` never caught it and a transient network
    blip to Anthropic 500'd the whole report instead of degrading it.
    """
    def _raise_conn(*a, **k):
        raise _connection_error()

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _raise_conn)
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    assert resp.json()["narrative"] is None

    db = TestingSessionLocal()
    try:
        assert db.query(Report).count() == 0
    finally:
        db.close()


def test_report_survives_anthropic_timeout(client, monkeypatch):
    """APITimeoutError is also outside APIStatusError."""
    import anthropic
    import httpx

    def _raise_timeout(*a, **k):
        raise anthropic.APITimeoutError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _raise_timeout)
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    assert resp.json()["narrative"] is None


def test_synthesis_is_retried_once_then_degrades(client, monkeypatch):
    """One try plus one retry - not zero retries, and not an infinite loop."""
    calls = []

    def _always_fail(*a, **k):
        calls.append(1)
        raise _connection_error()

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _always_fail)
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    client.get("/company/MSFT/report")
    assert len(calls) == 2


def test_no_sleep_after_the_final_attempt(client, monkeypatch):
    """Sleeping after the last failure delays the response for nothing."""
    sleeps = []

    def _always_fail(*a, **k):
        raise _connection_error()

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _always_fail)
    monkeypatch.setattr("main.time.sleep", lambda s: sleeps.append(s))

    client.get("/company/MSFT/report")
    assert len(sleeps) == 1, f"expected one sleep between two attempts, got {sleeps}"


def test_synthesis_succeeding_on_retry_is_cached(client, monkeypatch):
    """A retry that succeeds produces a real, cacheable report."""
    attempts = []

    def _fail_then_succeed(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise _connection_error()
        return {"verdict": "WATCH-CASE", "risks": [], "grounding_rate": 1.0}

    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda ticker: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _fail_then_succeed)
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    assert resp.json()["narrative"]["verdict"] == "WATCH-CASE"

    db = TestingSessionLocal()
    try:
        assert db.query(Report).count() == 1
    finally:
        db.close()


# --- 404 content type follows the route, not the path prefix ---
#
# The handler matched every path under /company/, so the JSON endpoints
# answered 404 with an HTML page. An API client asking for JSON got markup.

def test_json_report_404_stays_json(client, monkeypatch):
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    resp = client.get("/company/FAKE/report")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"]


def test_json_company_404_stays_json(client, monkeypatch):
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    resp = client.get("/company/FAKE")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_json_financials_404_stays_json(client, monkeypatch):
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    resp = client.get("/company/FAKE/financials")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_json_brief_404_stays_json(client, monkeypatch):
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    resp = client.get("/company/FAKE/brief")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_view_route_404_is_html(client, monkeypatch):
    """The human-facing route keeps its on-brand page when the ticker misses."""
    monkeypatch.setattr("ingest.get_cik", _raise_unknown)
    monkeypatch.setattr("main.get_cik", _raise_unknown)
    resp = client.get("/company/FAKE/report/view")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Not found" in resp.text


# --- SEC failures are upstream failures, not our bugs ---
#
# get_or_ingest_company caught only ValueError, but every SEC call ends in
# raise_for_status(). The SEC throttles at 10 req/s, so a 429 or a 503 is an
# ordinary event - and it surfaced as an unhandled 500 with a stack trace,
# which reads as "this application is broken" rather than "the source is busy".

import requests


def _sec_down(*a, **k):
    resp = requests.Response()
    resp.status_code = 429
    raise requests.HTTPError("429 Too Many Requests", response=resp)


def _sec_unreachable(*a, **k):
    raise requests.ConnectionError("connection refused")


def test_sec_rate_limit_during_ingest_is_502(client, monkeypatch):
    monkeypatch.setattr("main.ingest_company", _sec_down)
    resp = client.get("/company/NEWCO")
    assert resp.status_code == 502
    assert "EDGAR" in resp.json()["detail"]


def test_sec_unreachable_during_ingest_is_502(client, monkeypatch):
    monkeypatch.setattr("main.ingest_company", _sec_unreachable)
    resp = client.get("/company/NEWCO")
    assert resp.status_code == 502


def test_unknown_ticker_is_still_404_not_502(client, monkeypatch):
    """The 429 mapping must not swallow the genuine not-found case."""
    monkeypatch.setattr("main.ingest_company", _raise_unknown)
    resp = client.get("/company/FAKE")
    assert resp.status_code == 404


def test_brief_returns_502_when_sec_is_down(client, monkeypatch):
    """The filing IS the brief. Without it there is nothing to degrade to."""
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_risk_factors", _sec_down)
    resp = client.get("/company/MSFT/brief")
    assert resp.status_code == 502


def test_report_degrades_when_sec_filing_is_unavailable(client, monkeypatch):
    """The computed figures do not come from the filing, so they survive it.

    Consistent with how a synthesis failure is handled: give back what was
    computed, mark the narrative absent, and do not cache the degraded result.
    """
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _sec_down)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative"] is None
    assert body["data"]["ttm"]["revenue"] == 100.0
    assert body["sources"]["filing"] is None

    db = TestingSessionLocal()
    try:
        assert db.query(Report).count() == 0
    finally:
        db.close()


def test_report_degrades_when_cik_lookup_fails(client, monkeypatch):
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_cik", _sec_unreachable)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    assert resp.json()["narrative"] is None


# --- brief cache invalidation ---
#
# Reports have a 7-day TTL. Briefs had none and no refresh, so a brief
# generated against last year's 10-K was served forever - even though the row
# already stored the report_date that would have revealed it was stale.

from models import Brief as BriefModel


def _seed_brief(question: str, report_date: str, answer: str = "cached answer"):
    db = TestingSessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == "MSFT").one()
        db.add(BriefModel(
            company_id=company.id, question=question, answer=answer,
            addressed=True, quotes=json.dumps(["We face intense competition."]),
            grounding_rate=1.0, filing_url="https://example.com/old.htm",
            report_date=report_date,
        ))
        db.commit()
    finally:
        db.close()


def _patch_brief_boundaries(monkeypatch, report_date: str, answers: list):
    """Stub the SEC and the LLM; `answers` records each analysis call."""
    def _answer(question, source_text, client=None):
        answers.append(question)
        return {
            "addressed": True, "answer": "freshly generated",
            "quotes": ["We face intense competition."],
            "quote_checks": [True], "grounding_rate": 1.0, "raw": "{}",
        }

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.answer_question", _answer)
    monkeypatch.setattr("main.get_risk_factors", lambda cik: {
        "text": "ITEM 1A. RISK FACTORS We face intense competition.",
        "url": "https://example.com/new.htm",
        "filing_date": "2026-07-29", "report_date": report_date,
    })
    monkeypatch.setattr("main.find_latest_10k", lambda cik: {
        "url": "https://example.com/new.htm", "filing_date": "2026-07-29",
        "report_date": report_date, "accession": "x",
    })


def test_brief_cache_hit_when_filing_unchanged(client, monkeypatch):
    from main import DEFAULT_QUESTION
    _seed_brief(DEFAULT_QUESTION, "2025-06-30")
    answers = []
    _patch_brief_boundaries(monkeypatch, "2025-06-30", answers)

    resp = client.get("/company/MSFT/brief")
    assert resp.status_code == 200
    assert resp.json()["answer"] == "cached answer"
    assert answers == [], "an unchanged filing must not trigger a new analysis"


def test_brief_regenerates_when_a_newer_10k_is_filed(client, monkeypatch):
    """The regression: a brief pinned to a superseded filing, served forever."""
    from main import DEFAULT_QUESTION
    _seed_brief(DEFAULT_QUESTION, "2025-06-30")
    answers = []
    _patch_brief_boundaries(monkeypatch, "2026-06-30", answers)

    resp = client.get("/company/MSFT/brief")
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "freshly generated"
    assert body["report_date"] == "2026-06-30"
    assert len(answers) == 1


def test_brief_refresh_forces_regeneration(client, monkeypatch):
    from main import DEFAULT_QUESTION
    _seed_brief(DEFAULT_QUESTION, "2025-06-30")
    answers = []
    _patch_brief_boundaries(monkeypatch, "2025-06-30", answers)

    resp = client.get("/company/MSFT/brief?refresh=true")
    assert resp.status_code == 200
    assert resp.json()["answer"] == "freshly generated"
    assert len(answers) == 1


def test_brief_still_served_when_freshness_check_fails(client, monkeypatch):
    """If the SEC is unreachable, a cached brief beats no brief at all."""
    from main import DEFAULT_QUESTION
    _seed_brief(DEFAULT_QUESTION, "2025-06-30")
    answers = []
    _patch_brief_boundaries(monkeypatch, "2025-06-30", answers)
    monkeypatch.setattr("main.find_latest_10k", _sec_down)

    resp = client.get("/company/MSFT/brief")
    assert resp.status_code == 200
    assert resp.json()["answer"] == "cached answer"
    assert answers == []


# --- /health must be able to say no ---
#
# It returned {"status": "ok"} unconditionally, without touching the database.
# That is the one thing a health check must never do: a container healthcheck
# wired to it would report a service healthy while every real request 500'd on
# a dead connection pool.

from sqlalchemy.exc import OperationalError

from database import get_db as real_get_db


class _DeadSession:
    def execute(self, *a, **k):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def close(self):
        pass


def test_health_reports_degraded_when_database_is_unreachable(client):
    from main import app as real_app

    def _dead_db():
        yield _DeadSession()

    real_app.dependency_overrides[real_get_db] = _dead_db
    try:
        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
        assert resp.json()["database"] == "unreachable"
    finally:
        real_app.dependency_overrides.pop(real_get_db, None)


def test_health_actually_queries_the_database(client):
    """A health check that does not touch the dependency proves nothing."""
    executed = []

    class _WatchingSession:
        def execute(self, stmt, *a, **k):
            executed.append(str(stmt))
            return None

        def close(self):
            pass

    from main import app as real_app

    def _watching_db():
        yield _WatchingSession()

    real_app.dependency_overrides[real_get_db] = _watching_db
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert executed, "/health did not query the database at all"
        assert "SELECT 1" in executed[0]
    finally:
        real_app.dependency_overrides.pop(real_get_db, None)


# --- bounding the untrusted inputs ---
#
# ?question= went straight into a Text column carrying a unique btree index.
# Postgres caps a btree entry at 2704 bytes, and long random text does not
# compress, so a sufficiently long question raised
#   index row size 3016 exceeds btree version 4 maximum 2704
# as an unhandled 500. Verified empirically against the real schema.

import secrets
import string

RANDOM_ALPHABET = string.ascii_letters + string.digits


def _incompressible(n: int) -> str:
    return "".join(secrets.choice(RANDOM_ALPHABET) for _ in range(n))


def test_over_long_question_is_rejected_not_500(client):
    resp = client.get("/company/MSFT/brief", params={"question": _incompressible(3000)})
    assert resp.status_code == 422, (
        f"expected a validation rejection, got {resp.status_code} - an "
        "over-long question reaches the btree index and raises"
    )


def test_question_at_the_limit_is_accepted(client, monkeypatch):
    """The cap must not be so tight that real questions bounce."""
    monkeypatch.setattr(analysis, "answer_question", _fake_answer)
    monkeypatch.setattr("main.answer_question", _fake_answer)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))

    resp = client.get("/company/MSFT/brief", params={"question": "a" * 500})
    assert resp.status_code == 200


def test_ordinary_question_still_works(client, monkeypatch):
    monkeypatch.setattr(analysis, "answer_question", _fake_answer)
    monkeypatch.setattr("main.answer_question", _fake_answer)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.get_cik", lambda ticker: ("789019", "Microsoft"))

    resp = client.get("/company/MSFT/brief",
                      params={"question": "What are the main competitive risks?"})
    assert resp.status_code == 200


def test_absurd_ticker_is_rejected_at_the_edge(client):
    resp = client.get("/company/" + "A" * 200)
    assert resp.status_code == 422


def test_ticker_with_control_characters_is_rejected(client):
    resp = client.get("/company/AB%00CD")
    assert resp.status_code in (404, 422)


def test_real_tickers_with_dots_and_dashes_still_work(client, monkeypatch):
    """BRK.B and BF-B are real tickers; the pattern must not exclude them."""
    seen = []

    def _capture(t):
        seen.append(t)
        raise ValueError(f"Unknown ticker: {t}")

    monkeypatch.setattr("main.ingest_company", _capture)
    for ticker in ("BRK.B", "BF-B"):
        resp = client.get(f"/company/{ticker}")
        assert resp.status_code == 404, f"{ticker} was rejected by the pattern"
    assert seen == ["BRK.B", "BF-B"]


# --- rate limiting and the refresh lever ---
#
# /brief and /report each spend an Anthropic call plus an SEC fetch on a cache
# miss, and ?refresh= bypasses the cache outright. Unauthenticated and
# publicly reachable, that is a loop anyone can run to drain the API budget.

import ratelimit
from config import settings as app_settings


def test_rate_limit_returns_429_past_the_burst(client, monkeypatch):
    monkeypatch.setattr(app_settings, "rate_limit_burst", 3)
    monkeypatch.setattr(app_settings, "rate_limit_per_minute", 0.0)
    ratelimit.clear()

    codes = [client.get("/company/MSFT/report").status_code for _ in range(5)]
    assert 429 in codes, f"never rate limited: {codes}"
    assert codes.count(429) == 2, f"expected the last two to be limited: {codes}"


def test_rate_limited_response_has_retry_after(client, monkeypatch):
    monkeypatch.setattr(app_settings, "rate_limit_burst", 1)
    monkeypatch.setattr(app_settings, "rate_limit_per_minute", 6.0)
    ratelimit.clear()

    client.get("/company/MSFT/report")
    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "10"


def test_rate_limit_is_per_client(client, monkeypatch):
    monkeypatch.setattr(app_settings, "rate_limit_burst", 1)
    monkeypatch.setattr(app_settings, "rate_limit_per_minute", 0.0)
    ratelimit.clear()

    client.get("/company/MSFT/report", headers={"X-Forwarded-For": "1.1.1.1"})
    blocked = client.get("/company/MSFT/report", headers={"X-Forwarded-For": "1.1.1.1"})
    other = client.get("/company/MSFT/report", headers={"X-Forwarded-For": "2.2.2.2"})

    assert blocked.status_code == 429
    assert other.status_code != 429, "a different client must not inherit the limit"


def test_free_endpoints_are_not_rate_limited(client, monkeypatch):
    """Only the endpoints that spend money are limited."""
    monkeypatch.setattr(app_settings, "rate_limit_burst", 1)
    monkeypatch.setattr(app_settings, "rate_limit_per_minute", 0.0)
    ratelimit.clear()

    for _ in range(5):
        assert client.get("/health").status_code == 200
        assert client.get("/companies").status_code == 200


def test_refresh_is_open_when_no_token_is_configured(client, monkeypatch):
    """Local development and the existing tests must keep working."""
    monkeypatch.setattr(app_settings, "refresh_token", "")
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)

    assert client.get("/company/MSFT/report?refresh=true").status_code == 200


def test_refresh_without_token_is_403_when_configured(client, monkeypatch):
    monkeypatch.setattr(app_settings, "refresh_token", "s3cret")
    resp = client.get("/company/MSFT/report?refresh=true")
    assert resp.status_code == 403


def test_refresh_with_wrong_token_is_403(client, monkeypatch):
    monkeypatch.setattr(app_settings, "refresh_token", "s3cret")
    resp = client.get("/company/MSFT/report?refresh=true&token=wrong")
    assert resp.status_code == 403


def test_refresh_with_correct_token_is_allowed(client, monkeypatch):
    monkeypatch.setattr(app_settings, "refresh_token", "s3cret")
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)

    resp = client.get("/company/MSFT/report?refresh=true&token=s3cret")
    assert resp.status_code == 200


def test_normal_request_unaffected_when_token_is_configured(client, monkeypatch):
    """Protecting refresh must not require a token for ordinary reads."""
    monkeypatch.setattr(app_settings, "refresh_token", "s3cret")
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)

    assert client.get("/company/MSFT/report").status_code == 200


# --- query bounds ---
#
# build_report_data reads at most rows[:20] - four quarters for TTM, four more
# for the prior-year comparison, twenty for the margin-stability history. The
# endpoint loaded every quarter the company had ever filed.

from datetime import date as _date

from models import Financials


def _seed_quarters(n: int):
    db = TestingSessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == "MSFT").one()
        for i in range(n):
            year, month = 2026 - (i // 4), [3, 6, 9, 12][i % 4]
            db.add(Financials(
                company_id=company.id, period_end=_date(year, month, 28),
                revenue=100, net_income=30, free_cash_flow=25,
                total_debt=40, shareholders_equity=400,
            ))
        db.commit()
    finally:
        db.close()


def test_report_loads_only_the_quarters_it_uses(client, monkeypatch):
    _seed_quarters(40)
    seen = []

    def _capture(rows, price_data):
        seen.append(len(rows))
        return _fake_report_data(rows, price_data)

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)
    monkeypatch.setattr("main.build_report_data", _capture)

    client.get("/company/MSFT/report")
    assert seen == [20], f"loaded {seen[0]} rows to read at most 20"


def test_report_still_correct_with_fewer_quarters(client, monkeypatch):
    """The limit must not change behaviour for a young company."""
    _seed_quarters(3)
    seen = []

    def _capture(rows, price_data):
        seen.append(len(rows))
        return _fake_report_data(rows, price_data)

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)
    monkeypatch.setattr("main.build_report_data", _capture)

    client.get("/company/MSFT/report")
    assert seen == [3]


def test_report_rows_are_newest_first(client, monkeypatch):
    """build_report_data documents that rows arrive newest first; the LIMIT
    must not silently hand it the oldest 20 instead."""
    _seed_quarters(40)
    seen = []

    def _capture(rows, price_data):
        seen.append([r.period_end for r in rows])
        return _fake_report_data(rows, price_data)

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)
    monkeypatch.setattr("main.build_report_data", _capture)

    client.get("/company/MSFT/report")
    dates = seen[0]
    assert dates == sorted(dates, reverse=True)
    assert dates[0].year == 2026, "should be the most recent quarters, not the oldest"


def _seed_companies(n: int):
    db = TestingSessionLocal()
    try:
        for i in range(n):
            db.add(Company(ticker=f"T{i:03d}", name=f"Company {i}"))
        db.commit()
    finally:
        db.close()


def test_companies_is_paginated(client):
    _seed_companies(30)
    resp = client.get("/companies?limit=10")
    assert resp.status_code == 200
    assert len(resp.json()) == 10


def test_companies_offset_advances(client):
    _seed_companies(30)
    first = client.get("/companies?limit=5").json()
    second = client.get("/companies?limit=5&offset=5").json()
    assert [c["ticker"] for c in first] != [c["ticker"] for c in second]


def test_companies_has_a_default_bound(client):
    """An unbounded list endpoint is a slow query waiting to happen."""
    _seed_companies(300)
    resp = client.get("/companies")
    assert len(resp.json()) <= 100


def test_companies_ordering_is_stable(client):
    """Pagination over an unordered query can repeat or skip rows."""
    _seed_companies(30)
    a = [c["ticker"] for c in client.get("/companies?limit=10").json()]
    b = [c["ticker"] for c in client.get("/companies?limit=10").json()]
    assert a == b


# --- HTTP caching on the report routes ---

def _cacheable_report(client, monkeypatch):
    """Generate and persist one report, returning its response."""
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: {
        "verdict": "WATCH-CASE", "risks": [], "grounding_rate": 1.0,
    })
    return client.get("/company/MSFT/report")


def test_report_sends_an_etag(client, monkeypatch):
    resp = _cacheable_report(client, monkeypatch)
    assert resp.status_code == 200
    assert resp.headers.get("ETag")


def test_report_sends_cache_control(client, monkeypatch):
    resp = _cacheable_report(client, monkeypatch)
    assert "max-age" in resp.headers.get("Cache-Control", "")


def test_matching_etag_returns_304(client, monkeypatch):
    first = _cacheable_report(client, monkeypatch)
    etag = first.headers["ETag"]

    second = client.get("/company/MSFT/report", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b"", "a 304 must not carry a body"


def test_stale_etag_returns_the_report(client, monkeypatch):
    _cacheable_report(client, monkeypatch)
    resp = client.get("/company/MSFT/report",
                      headers={"If-None-Match": '"not-the-right-etag"'})
    assert resp.status_code == 200
    assert resp.json()["company"] == "MSFT"


def test_etag_is_stable_across_requests(client, monkeypatch):
    """A validator that changes every request never produces a 304."""
    first = _cacheable_report(client, monkeypatch)
    second = client.get("/company/MSFT/report")
    assert first.headers["ETag"] == second.headers["ETag"]


def test_degraded_report_is_not_client_cacheable(client, monkeypatch):
    """A degraded report is deliberately not persisted so the next request
    retries; letting a browser cache it would defeat exactly that."""
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", lambda *a, **k: None)

    resp = client.get("/company/MSFT/report")
    assert resp.status_code == 200
    assert resp.json()["narrative"] is None
    assert resp.headers.get("Cache-Control") == "no-store"


def test_view_route_also_supports_conditional_requests(client, monkeypatch):
    _cacheable_report(client, monkeypatch)
    first = client.get("/company/MSFT/report/view")
    assert first.status_code == 200
    assert first.headers.get("ETag")

    second = client.get("/company/MSFT/report/view",
                        headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304


def test_view_route_still_returns_html(client, monkeypatch):
    _cacheable_report(client, monkeypatch)
    resp = client.get("/company/MSFT/report/view")
    assert resp.headers["content-type"].startswith("text/html")
    assert "WATCH-CASE" in resp.text


def test_generated_at_is_utc_on_a_cache_hit(client, monkeypatch):
    """Postgres returns timestamptz in the session timezone, so a cached
    report's generated_at came back with a local offset while a freshly built
    one was UTC - the same field, two representations."""
    _cacheable_report(client, monkeypatch)
    cached = client.get("/company/MSFT/report").json()
    assert cached["cache"]["cached"] is True
    assert cached["cache"]["generated_at"].endswith("+00:00"), (
        f"not UTC: {cached['cache']['generated_at']}"
    )


def test_generated_at_is_utc_on_a_fresh_build(client, monkeypatch):
    fresh = _cacheable_report(client, monkeypatch).json()
    assert fresh["cache"]["generated_at"].endswith("+00:00")


# --- retry classification ---
#
# The handler caught every APIError, so a usage-limit or auth failure was
# retried: a second doomed call plus a backoff sleep for an error that cannot
# succeed. Measured cost of a pointless second synthesis attempt: ~32 seconds.

import httpx as _httpx


def _api_status_error(status: int):
    import anthropic

    response = _httpx.Response(
        status, request=_httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    mapping = {
        400: anthropic.BadRequestError, 401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError, 404: anthropic.NotFoundError,
        422: anthropic.UnprocessableEntityError, 429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
    }
    cls = mapping.get(status, anthropic.APIStatusError)
    return cls("boom", response=response, body=None)


def _count_synthesis_attempts(client, monkeypatch, raiser):
    calls, sleeps = [], []

    def _attempt(*a, **k):
        calls.append(1)
        raise raiser()

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _attempt)
    monkeypatch.setattr("main.time.sleep", lambda s: sleeps.append(s))

    resp = client.get("/company/MSFT/report")
    return resp, len(calls), len(sleeps)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_status_makes_exactly_one_attempt(client, monkeypatch, status):
    """The request is wrong; repeating it cannot make it right."""
    resp, attempts, sleeps = _count_synthesis_attempts(
        client, monkeypatch, lambda: _api_status_error(status)
    )
    assert resp.status_code == 200
    assert resp.json()["narrative"] is None
    assert attempts == 1, f"status {status} was retried"
    assert sleeps == 0, f"status {status} slept before giving up"


@pytest.mark.parametrize("status", [429, 500, 503, 529])
def test_transient_status_is_retried(client, monkeypatch, status):
    """Rate limits and server errors are exactly what a retry is for."""
    resp, attempts, sleeps = _count_synthesis_attempts(
        client, monkeypatch, lambda: _api_status_error(status)
    )
    assert resp.status_code == 200
    assert attempts == 2, f"status {status} was not retried"
    assert sleeps == 1


def test_connection_error_is_retried(client, monkeypatch):
    _, attempts, _ = _count_synthesis_attempts(
        client, monkeypatch, _connection_error
    )
    assert attempts == 2


def test_timeout_is_retried(client, monkeypatch):
    import anthropic

    def _timeout():
        return anthropic.APITimeoutError(
            request=_httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )

    _, attempts, _ = _count_synthesis_attempts(client, monkeypatch, _timeout)
    assert attempts == 2


def test_malformed_json_is_retried(client, monkeypatch):
    """A different sample may parse, so this one is worth repeating."""
    from report import SynthesisError

    def _bad_json():
        return SynthesisError("not valid JSON", raw="x")

    _, attempts, _ = _count_synthesis_attempts(client, monkeypatch, _bad_json)
    assert attempts == 2


def test_truncation_is_not_retried(client, monkeypatch):
    """Deterministic: the same prompt and cap truncate at the same place.

    Observed on NVDA - both attempts failed identically and the report
    degraded after two full 32-second calls.
    """
    from report import SynthesisTruncated

    def _truncated():
        return SynthesisTruncated("hit max_tokens", raw="x")

    resp, attempts, sleeps = _count_synthesis_attempts(client, monkeypatch, _truncated)
    assert resp.status_code == 200
    assert resp.json()["narrative"] is None
    assert attempts == 1, "a truncated response was retried"
    assert sleeps == 0


def test_a_retry_that_succeeds_still_produces_a_report(client, monkeypatch):
    """Fail-fast must not have broken the case retries exist for."""
    attempts = []

    def _fail_then_succeed(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise _api_status_error(529)
        return {"verdict": "BUY-CASE", "risks": [], "grounding_rate": 1.0}

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.synthesize", _fail_then_succeed)
    monkeypatch.setattr("main.time.sleep", lambda s: None)

    resp = client.get("/company/MSFT/report")
    assert resp.json()["narrative"]["verdict"] == "BUY-CASE"
    assert len(attempts) == 2


# --- the build as a sequence of events ---
#
# One generator, two consumers: the JSON endpoint keeps only the Result, the
# stream reports each stage as it lands. Implementing that twice would
# guarantee the two drift.

import pipeline as pipeline_module
from main import _report_events, _synthesis_failure_detail


def _drive(client, monkeypatch, ticker="MSFT", synth=None, filing=_fake_risk_factors):
    from fastapi import Response as _Response

    from conftest import TestingSessionLocal as _S
    from models import Company as _C

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", filing)
    monkeypatch.setattr("main.time.sleep", lambda s: None)
    if synth is not None:
        monkeypatch.setattr("main.synthesize", synth)

    db = _S()
    try:
        company = db.query(_C).filter(_C.ticker == ticker).first()
        return list(_report_events(_Response(), ticker, company, False, db))
    finally:
        db.close()


def _stage_keys(events):
    return [e.key for e in events if isinstance(e, pipeline_module.Stage)]


def test_events_arrive_in_pipeline_order(client, monkeypatch):
    events = _drive(client, monkeypatch,
                    synth=lambda *a, **k: {"verdict": "BUY-CASE", "risks": []})
    assert _stage_keys(events) == ["fetch", "fetch", "store", "metrics",
                                   "metrics", "synthesis", "synthesis"]


def test_a_known_company_skips_the_store_stage(client, monkeypatch):
    """MSFT is seeded, so there is nothing to ingest."""
    events = _drive(client, monkeypatch,
                    synth=lambda *a, **k: {"verdict": "BUY-CASE", "risks": []})
    store = [e for e in events if isinstance(e, pipeline_module.Stage)
             and e.key == "store"]
    assert [e.state for e in store] == ["skipped"]


def test_partial_arrives_before_synthesis_starts(client, monkeypatch):
    """The whole point: figures on the page while the model is still writing."""
    events = _drive(client, monkeypatch,
                    synth=lambda *a, **k: {"verdict": "BUY-CASE", "risks": []})
    kinds = [type(e).__name__ for e in events]
    partial_at = kinds.index("Partial")
    synthesis_at = next(
        i for i, e in enumerate(events)
        if isinstance(e, pipeline_module.Stage) and e.key == "synthesis"
    )
    assert partial_at < synthesis_at


def test_partial_carries_the_computed_figures_and_no_narrative(client, monkeypatch):
    events = _drive(client, monkeypatch,
                    synth=lambda *a, **k: {"verdict": "BUY-CASE", "risks": []})
    partial = next(e for e in events if isinstance(e, pipeline_module.Partial))
    assert partial.payload["narrative"] is None
    assert partial.payload["data"]["ttm"]["revenue"] == 100.0
    assert partial.payload["company"] == "MSFT"


def test_result_is_the_last_event(client, monkeypatch):
    events = _drive(client, monkeypatch,
                    synth=lambda *a, **k: {"verdict": "BUY-CASE", "risks": []})
    assert isinstance(events[-1], pipeline_module.Result)
    assert events[-1].payload["narrative"]["verdict"] == "BUY-CASE"


def test_synthesis_failure_marks_the_stage_failed_with_a_reason(client, monkeypatch):
    def _boom(*a, **k):
        raise _api_status_error(429)

    events = _drive(client, monkeypatch, synth=_boom)
    synth = [e for e in events if isinstance(e, pipeline_module.Stage)
             and e.key == "synthesis"]
    assert synth[-1].state == "failed"
    assert "rate limited" in synth[-1].detail
    assert events[-1].payload["narrative"] is None


def test_missing_filing_marks_synthesis_failed(client, monkeypatch):
    events = _drive(client, monkeypatch, filing=lambda cik: None)
    synth = [e for e in events if isinstance(e, pipeline_module.Stage)
             and e.key == "synthesis"]
    assert synth[-1].state == "failed"
    assert "10-K" in synth[-1].detail


def test_stages_report_how_long_they_took(client, monkeypatch):
    events = _drive(client, monkeypatch,
                    synth=lambda *a, **k: {"verdict": "BUY-CASE", "risks": []})
    done = [e for e in events if isinstance(e, pipeline_module.Stage)
            and e.state == "done"]
    assert done and all(e.seconds is not None for e in done)


# --- failure messages are for people ---

@pytest.mark.parametrize("status,expected", [
    (429, "rate limited"),
    (401, "credentials"),
    (403, "credentials"),
    (400, "usage limit"),
    (500, "unavailable"),
])
def test_failure_detail_explains_the_cause(status, expected):
    assert expected in _synthesis_failure_detail(_api_status_error(status))


def test_truncation_has_its_own_message():
    from report import SynthesisTruncated

    detail = _synthesis_failure_detail(SynthesisTruncated("hit max_tokens"))
    assert "length limit" in detail


def test_unknown_failure_still_says_something():
    assert _synthesis_failure_detail(RuntimeError("???"))


# --- the streaming endpoint ---
#
# The page opens this as an EventSource and rebuilds itself as stages land.
# Everything the reader sees during a cold build comes through here, so every
# outcome - including every failure - has to arrive as an event.

def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Return [(event, data)] from a text/event-stream body."""
    out = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name:
            out.append((name, data))
    return out


def _stream(client, monkeypatch, ticker="MSFT", synth=None,
            filing=_fake_risk_factors):
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.build_report_data", _fake_report_data)
    monkeypatch.setattr("main.get_risk_factors", filing)
    monkeypatch.setattr("main.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "main.synthesize",
        synth or (lambda *a, **k: {"verdict": "WATCH-CASE", "risks": [],
                                   "grounding_rate": 1.0}),
    )
    resp = client.get(f"/company/{ticker}/report/stream")
    return resp, _parse_sse(resp.text)


def test_stream_is_an_event_stream(client, monkeypatch):
    resp, _ = _stream(client, monkeypatch)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_stream_is_never_cached(client, monkeypatch):
    """Caching a progress stream would replay a stale build."""
    resp, _ = _stream(client, monkeypatch)
    assert resp.headers["cache-control"] == "no-store"


def test_stream_disables_proxy_buffering(client, monkeypatch):
    """A buffering proxy holds every event until the end, defeating the point."""
    resp, _ = _stream(client, monkeypatch)
    assert resp.headers["x-accel-buffering"] == "no"


def test_stream_reports_stages_then_partial_then_done(client, monkeypatch):
    _, events = _stream(client, monkeypatch)
    names = [name for name, _ in events]
    assert "stage" in names
    assert names.index("partial") < names.index("done")
    assert names[-1] == "done"


def test_partial_arrives_before_the_narrative_exists(client, monkeypatch):
    """The reason this endpoint exists: real figures long before the verdict."""
    _, events = _stream(client, monkeypatch)
    partial = next(d for n, d in events if n == "partial")
    assert "$100" in partial["html"] or "Scorecard" in partial["html"]
    assert "Writing analysis" in partial["html"]
    assert "WATCH-CASE" not in partial["html"]


def test_done_carries_the_finished_sheet(client, monkeypatch):
    _, events = _stream(client, monkeypatch)
    done = next(d for n, d in events if n == "done")
    assert "WATCH-CASE" in done["html"]
    assert "Writing analysis" not in done["html"]


def test_done_carries_html_not_a_reload_instruction(client, monkeypatch):
    """A degraded report is not cached, so a reload would rebuild everything."""
    _, events = _stream(client, monkeypatch)
    done = next(d for n, d in events if n == "done")
    assert done["html"].lstrip().startswith("<div")


def test_stage_events_carry_state_and_timing(client, monkeypatch):
    _, events = _stream(client, monkeypatch)
    stages = [d for n, d in events if n == "stage"]
    assert {"key", "label", "state"} <= set(stages[0])
    assert any(s.get("seconds") is not None for s in stages)


def test_every_stage_reaches_a_terminal_state(client, monkeypatch):
    """A stage left 'running' is a spinner that never stops."""
    _, events = _stream(client, monkeypatch)
    final = {}
    for name, data in events:
        if name == "stage":
            final[data["key"]] = data["state"]
    assert set(final) == {"fetch", "store", "metrics", "synthesis"}
    assert all(state != "running" for state in final.values()), final


def test_unknown_ticker_streams_a_failure(client, monkeypatch):
    monkeypatch.setattr("main.fetch_financials", _raise_unknown)
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "X"))

    resp = client.get("/company/NEWCO/report/stream")
    events = _parse_sse(resp.text)
    assert events[-1][0] == "failed"
    assert events[-1][1]["status"] == 404
    assert "Not found" in events[-1][1]["html"]


def test_sec_outage_streams_a_failure(client, monkeypatch):
    monkeypatch.setattr("main.fetch_financials", _sec_down)
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "X"))

    events = _parse_sse(client.get("/company/NEWCO/report/stream").text)
    assert events[-1][0] == "failed"
    assert events[-1][1]["status"] == 502
    assert "EDGAR" in events[-1][1]["html"]


def test_synthesis_failure_still_delivers_the_figures(client, monkeypatch):
    """A failed narrative must not cost the reader the computed data."""
    def _boom(*a, **k):
        raise _api_status_error(400)

    _, events = _stream(client, monkeypatch, synth=_boom)
    names = [n for n, _ in events]
    assert "partial" in names
    assert names[-1] == "done"
    synth = [d for n, d in events if n == "stage" and d["key"] == "synthesis"]
    assert synth[-1]["state"] == "failed"
    assert "usage limit" in synth[-1]["detail"]


def test_an_unexpected_error_still_ends_the_stream(client, monkeypatch):
    """Nothing may escape a streaming response: the page would wait forever."""
    def _explode(*a, **k):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.get_price", lambda t: None)
    monkeypatch.setattr("main.get_risk_factors", _fake_risk_factors)
    monkeypatch.setattr("main.build_report_data", _explode)

    events = _parse_sse(client.get("/company/MSFT/report/stream").text)
    assert events[-1][0] == "failed"
    assert events[-1][1]["status"] == 500


def test_stream_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(app_settings, "rate_limit_burst", 1)
    monkeypatch.setattr(app_settings, "rate_limit_per_minute", 0.0)
    ratelimit.clear()
    _stream(client, monkeypatch)
    assert client.get("/company/MSFT/report/stream").status_code == 429


# --- the view route now serves a shell on a cold build ---

def test_view_route_serves_the_shell_when_uncached(client, monkeypatch):
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    resp = client.get("/company/MSFT/report/view")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "EventSource" in resp.text
    assert 'data-stage="fetch"' in resp.text


def test_the_shell_is_not_cached(client, monkeypatch):
    """It represents work in progress, not a result."""
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    resp = client.get("/company/MSFT/report/view")
    assert resp.headers["cache-control"] == "no-store"


def test_the_shell_arrives_without_touching_the_model(client, monkeypatch):
    """The whole point: the first response does not wait for synthesis."""
    called = []
    monkeypatch.setattr("main.get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr("main.synthesize", lambda *a, **k: called.append(1))
    client.get("/company/MSFT/report/view")
    assert called == []


def test_cached_report_skips_the_shell_entirely(client, monkeypatch):
    """A report already built should render at once, not stream again."""
    _cacheable_report(client, monkeypatch)
    resp = client.get("/company/MSFT/report/view")
    assert resp.status_code == 200
    assert "EventSource" not in resp.text
    assert "WATCH-CASE" in resp.text
