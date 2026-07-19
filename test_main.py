from fastapi.testclient import TestClient
from main import app

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