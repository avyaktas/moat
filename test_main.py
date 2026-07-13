from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_get_company():
    response = client.get("/company/MSFT")
    assert response.status_code == 200
    assert response.json()["ticker"] == "MSFT"

def test_company_lowercase():
    response = client.get("/company/msft")
    assert response.status_code == 200
    assert response.json()["ticker"] == "MSFT"

def test_get_company_not_found():
    response = client.get("/company/FAKE")
    assert response.status_code == 404