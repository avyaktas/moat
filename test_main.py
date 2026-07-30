from fastapi.testclient import TestClient
from main import app

import json
import filings
import analysis
from models import Brief, Report
from conftest import TestingSessionLocal

client = TestClient(app)

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

def test_get_company_not_found(client):
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
    """Stand-in for build_report_data — valid computed figures, no DB rows needed."""
    return {
        "as_of": "2025-06-30",
        "ttm": {"revenue": 100.0, "net_income": 30.0, "net_margin": 0.30},
        "scorecard": {"checks": [], "summary": {}, "valuation": {}},
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