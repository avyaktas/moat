"""Shared test fixtures.

WHY THE SCHEMA IS BUILT ONCE

    Every test used to call Base.metadata.create_all and drop_all, so the
    whole schema was created and destroyed 293 times per run - five DDL
    transactions per test to produce tables that are identical every time.

    The schema is now built once per session and the tables are emptied
    between tests with a single TRUNCATE. Each test still starts from exactly
    the same state: empty tables, sequences reset, one seeded company. The
    isolation is unchanged; only the cost is.

    The alternative - binding every session to one connection and rolling back
    a transaction per test - is faster still, but it requires all database
    access to travel through that connection. Several tests deliberately use a
    separate session to verify what the application actually committed, which
    is a stronger assertion than checking what it merely wrote, so that
    trade-off is not worth making here.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import settings
from database import get_db
from main import app
from models import Base, Company

TEST_DATABASE_URL = settings.test_database_url

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build the schema once for the whole run, and remove it at the end."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def _truncate_all() -> None:
    """Empty every table in one statement.

    RESTART IDENTITY resets the primary-key sequences, so ids are stable from
    test to test rather than climbing across the run - which matters because a
    test asserting on a specific id would otherwise pass alone and fail in a
    suite. CASCADE handles the foreign keys from financials, briefs and
    reports back to companies.
    """
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    # Pristine tables, seeded with the known company, then hand out a client.
    _truncate_all()

    db = TestingSessionLocal()
    db.add(Company(ticker="MSFT", name="Microsoft", sector="Technology"))
    db.commit()
    db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _isolate_process_caches():
    """Reset in-process state between tests.

    The rate limiter, the price cache and the filing-metadata cache are all
    module-level dicts that outlive a single test. Without this, one test's
    requests would consume another's rate-limit budget and cached prices
    would leak across cases - the tests would pass or fail depending on the
    order they ran in.
    """
    import filings
    import prices
    import ratelimit

    ratelimit.clear()
    prices.clear_price_cache()
    filings.clear_filing_caches()
    yield


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if a test reaches the network.

    The suite is meant to be fast, deterministic and runnable offline, and
    every upstream is supposed to be mocked. Nothing enforced that, so
    breaches accumulated quietly: a file named test_edgar.py fired a live SEC
    request on every collection, and the rate-limit tests reached yfinance
    through an unmocked get_price - paying real latency and making the suite
    depend on two third parties being up.

    Blocking at the transport layer catches it wherever it comes from, rather
    than requiring each test to remember every boundary. A test that wants a
    stubbed upstream still mocks at the level it cares about; this only fires
    when nothing mocked anything.
    """
    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "This test tried to make a real network request. Mock the "
            "boundary it needs - get_price, get_risk_factors, find_latest_10k, "
            "fetch_company_facts or get_cik."
        )

    import prices
    import requests.sessions

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
    # yfinance talks through curl_cffi, not requests, so blocking the requests
    # transport alone misses it entirely - which is how the rate-limit tests
    # were reaching the live market-data API without anyone noticing.
    monkeypatch.setattr(prices, "_fetch_price", _blocked)
    yield
