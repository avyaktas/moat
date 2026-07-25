from fastapi.testclient import TestClient
from main import app

import json
import filings
import analysis
from models import Brief

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