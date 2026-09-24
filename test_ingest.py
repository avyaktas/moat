"""Tests for the ingestion pipeline's pure extraction/derivation logic.

These need no network and no database - they feed synthetic EDGAR-shaped
data through the extractors and assert the standalone quarters that come
out. The focus here is Task 4: recovering interim quarters for filers that
report cash flow year-to-date within the fiscal year (Apple, IBM).
"""

from datetime import date

from ingest import (
    derive_interim_quarters,
    extract_quarterly,
    extract_ytd,
)


def _entry(start: str, end: str, val: float, frame: str | None = None,
           form: str = "10-Q") -> dict:
    e = {"start": start, "end": end, "val": val, "form": form}
    if frame is not None:
        e["frame"] = frame
    return e


def _facts(entries: list[dict], tag: str = "OCF") -> dict:
    return {"facts": {"us-gaap": {tag: {"units": {"USD": entries}}}}}


# A synthetic cumulative filer: fiscal year = calendar 2024. Only Q1 carries a
# standalone-quarter frame; Q2/Q3/FY are reported as running totals from Jan 1.
CUMULATIVE = [
    _entry("2024-01-01", "2024-03-31", 100.0, frame="CY2024Q1"),  # Q1        90d
    _entry("2024-01-01", "2024-06-30", 250.0),                    # YTD Q2   181d
    _entry("2024-01-01", "2024-09-30", 420.0),                    # YTD Q3   273d
    _entry("2024-01-01", "2024-12-31", 600.0, form="10-K"),       # FY       365d
]


def test_extract_quarterly_only_sees_framed_quarter():
    # A cumulative filer's Q2/Q3 have no standalone frame, so today's
    # extractor sees only Q1 - which is the null-FCF problem Task 4 fixes.
    q = extract_quarterly(_facts(CUMULATIVE), ["OCF"])
    assert q == {date(2024, 3, 31): 100.0}


def test_extract_ytd_returns_the_running_totals():
    ytd = {end: val for _, end, val in extract_ytd(_facts(CUMULATIVE), ["OCF"])}
    assert ytd[date(2024, 3, 31)] == 100.0
    assert ytd[date(2024, 6, 30)] == 250.0
    assert ytd[date(2024, 9, 30)] == 420.0
    assert ytd[date(2024, 12, 31)] == 600.0


def test_interim_quarters_recovered_by_differencing():
    q = extract_quarterly(_facts(CUMULATIVE), ["OCF"])
    q = derive_interim_quarters(q, extract_ytd(_facts(CUMULATIVE), ["OCF"]))
    assert q[date(2024, 3, 31)] == 100.0            # Q1, untouched
    assert q[date(2024, 6, 30)] == 150.0            # 250 - 100
    assert q[date(2024, 9, 30)] == 170.0            # 420 - 250
    # The annual is left to derive_q4, not filled here.
    assert date(2024, 12, 31) not in q


def test_missing_middle_period_yields_no_estimate():
    # YTD(Q2) is absent. Q2 can't be derived, and neither can Q3 (it would
    # need YTD(Q2)). Both must stay absent rather than be fabricated.
    entries = [
        _entry("2024-01-01", "2024-03-31", 100.0, frame="CY2024Q1"),  # Q1     90d
        _entry("2024-01-01", "2024-09-30", 420.0),                    # YTD Q3 273d
    ]
    q = extract_quarterly(_facts(entries), ["OCF"])
    q = derive_interim_quarters(q, extract_ytd(_facts(entries), ["OCF"]))
    assert q[date(2024, 3, 31)] == 100.0
    assert date(2024, 6, 30) not in q   # Q2 never existed
    assert date(2024, 9, 30) not in q   # Q3 not derivable across the gap


def test_discrete_filer_is_a_no_op():
    # A discrete filer already publishes a standalone Q2. Differencing must
    # never overwrite the real filed value with a reconstructed one.
    real_q2 = {date(2024, 6, 30): 999.0}
    q = derive_interim_quarters(real_q2, extract_ytd(_facts(CUMULATIVE), ["OCF"]))
    assert q[date(2024, 6, 30)] == 999.0


def test_first_member_not_treated_as_quarter_when_it_is_a_half_year():
    # If the earliest available YTD point already spans two quarters, it is not
    # a standalone quarter and must not be recorded as one.
    entries = [
        _entry("2024-01-01", "2024-06-30", 250.0),   # first point is 181d
        _entry("2024-01-01", "2024-09-30", 420.0),   # step 92d -> Q3 derivable
    ]
    q = derive_interim_quarters({}, extract_ytd(_facts(entries), ["OCF"]))
    assert date(2024, 6, 30) not in q            # not a standalone quarter
    assert q[date(2024, 9, 30)] == 170.0         # 420 - 250, one clean step


# --- ticker normalization at the ingest boundary ---
#
# ingest_company queried Company.ticker against its raw argument. The API path
# uppercases before calling, so this never showed there - but the module's own
# CLI (`python ingest.py msft`) wrote a lowercase row. A later /company/MSFT
# then failed to match it, tried to insert MSFT, and the unique constraint
# turned a case difference into a 500.

import ingest as ingest_module
from conftest import TestingSessionLocal
from models import Company


def _stub_edgar(monkeypatch, name: str = "Microsoft"):
    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", name))
    monkeypatch.setattr(
        ingest_module, "fetch_company_facts", lambda cik: {"facts": {"us-gaap": {}}}
    )


def test_ingest_lowercase_ticker_reuses_the_existing_company(client, monkeypatch):
    # The client fixture seeds MSFT. Ingesting "msft" must find that row,
    # not create a second company differing only in case.
    _stub_edgar(monkeypatch)
    ingest_module.ingest_company("msft")

    db = TestingSessionLocal()
    try:
        tickers = sorted(c.ticker for c in db.query(Company).all())
        assert tickers == ["MSFT"], f"expected one MSFT row, got {tickers}"
    finally:
        db.close()


def test_ingest_stores_new_ticker_uppercased(client, monkeypatch):
    _stub_edgar(monkeypatch, name="Alphabet")
    ingest_module.ingest_company("googl")

    db = TestingSessionLocal()
    try:
        tickers = sorted(c.ticker for c in db.query(Company).all())
        assert "GOOGL" in tickers
        assert "googl" not in tickers
    finally:
        db.close()


def test_ingest_is_idempotent_across_case(client, monkeypatch):
    _stub_edgar(monkeypatch)
    ingest_module.ingest_company("MSFT")
    ingest_module.ingest_company("msft")
    ingest_module.ingest_company("Msft")

    db = TestingSessionLocal()
    try:
        assert db.query(Company).count() == 1
    finally:
        db.close()


# ---------------------------------------------------------------- derive_q4
#
# Companies do not file a standalone Q4 10-Q; the fourth quarter lives inside
# the annual 10-K figure. derive_q4 recovers it by subtraction - real
# arithmetic on filed numbers, not an estimate. It was untested, and it is the
# most consequential untested code in the repo: a bug here is a wrong revenue
# figure on a published tearsheet, arrived at silently.

from ingest import derive_q4, extract_annual


def test_q4_is_the_year_minus_three_quarters():
    quarterly = {
        date(2024, 3, 31): 100.0,
        date(2024, 6, 30): 150.0,
        date(2024, 9, 30): 170.0,
    }
    annual = {date(2024, 12, 31): (date(2024, 1, 1), 600.0)}
    out = derive_q4(quarterly, annual)
    assert out[date(2024, 12, 31)] == 180.0      # 600 - (100 + 150 + 170)


def test_q4_not_derived_from_two_quarters():
    """Three quarters exactly, or nothing. Two would silently overstate Q4."""
    quarterly = {date(2024, 3, 31): 100.0, date(2024, 6, 30): 150.0}
    annual = {date(2024, 12, 31): (date(2024, 1, 1), 600.0)}
    assert date(2024, 12, 31) not in derive_q4(quarterly, annual)


def test_q4_not_derived_from_four_quarters():
    """Four quarters inside the year means one is already Q4 or the data is
    wrong; subtracting would produce a fifth quarter from nowhere."""
    quarterly = {
        date(2024, 3, 31): 100.0, date(2024, 6, 30): 150.0,
        date(2024, 9, 30): 170.0, date(2024, 11, 30): 50.0,
    }
    annual = {date(2025, 1, 31): (date(2024, 1, 1), 600.0)}
    assert date(2025, 1, 31) not in derive_q4(quarterly, annual)


def test_existing_q4_is_never_overwritten():
    """A filed Q4 is a fact; a derived one is arithmetic. Facts win."""
    quarterly = {
        date(2024, 3, 31): 100.0, date(2024, 6, 30): 150.0,
        date(2024, 9, 30): 170.0, date(2024, 12, 31): 999.0,
    }
    annual = {date(2024, 12, 31): (date(2024, 1, 1), 600.0)}
    assert derive_q4(quarterly, annual)[date(2024, 12, 31)] == 999.0


def test_q4_ignores_quarters_outside_the_fiscal_year():
    """Only quarters inside [fy_start, fy_end] count toward the subtraction."""
    quarterly = {
        date(2023, 12, 31): 500.0,                # prior year, must be ignored
        date(2024, 3, 31): 100.0,
        date(2024, 6, 30): 150.0,
        date(2024, 9, 30): 170.0,
    }
    annual = {date(2024, 12, 31): (date(2024, 1, 1), 600.0)}
    assert derive_q4(quarterly, annual)[date(2024, 12, 31)] == 180.0


def test_q4_handles_an_off_calendar_fiscal_year():
    """Microsoft's fiscal year ends 30 June."""
    quarterly = {
        date(2024, 9, 30): 100.0,
        date(2024, 12, 31): 150.0,
        date(2025, 3, 31): 170.0,
    }
    annual = {date(2025, 6, 30): (date(2024, 7, 1), 600.0)}
    assert derive_q4(quarterly, annual)[date(2025, 6, 30)] == 180.0


def test_q4_can_be_negative():
    """A loss-making fourth quarter is a real outcome, not a bad derivation."""
    quarterly = {
        date(2024, 3, 31): 100.0, date(2024, 6, 30): 100.0,
        date(2024, 9, 30): 100.0,
    }
    annual = {date(2024, 12, 31): (date(2024, 1, 1), 250.0)}
    assert derive_q4(quarterly, annual)[date(2024, 12, 31)] == -50.0


def test_q4_with_no_annual_data_derives_nothing():
    quarterly = {date(2024, 3, 31): 100.0}
    assert derive_q4(quarterly, {}) == quarterly


# ---------------------------------------------------------------- extract_annual

def test_extract_annual_takes_only_10k_entries():
    facts = _facts([
        _entry("2024-01-01", "2024-12-31", 600.0, form="10-K"),
        _entry("2023-01-01", "2023-12-31", 500.0, form="10-Q"),
    ])
    out = extract_annual(facts, ["OCF"])
    assert date(2024, 12, 31) in out
    assert date(2023, 12, 31) not in out


def test_extract_annual_tolerates_52_and_53_week_years():
    """Retailers run 52/53-week fiscal calendars; 364 and 371 days are years."""
    facts = _facts([
        _entry("2024-01-01", "2024-12-29", 600.0, form="10-K"),   # 363 days
        _entry("2022-01-02", "2023-01-07", 500.0, form="10-K"),   # 370 days
    ])
    out = extract_annual(facts, ["OCF"])
    assert len(out) == 2


def test_extract_annual_rejects_a_half_year():
    facts = _facts([_entry("2024-01-01", "2024-06-30", 300.0, form="10-K")])
    assert extract_annual(facts, ["OCF"]) == {}


def test_extract_annual_returns_the_start_date():
    """derive_q4 needs the start to know which quarters fall inside the year."""
    facts = _facts([_entry("2024-01-01", "2024-12-31", 600.0, form="10-K")])
    start, value = extract_annual(facts, ["OCF"])[date(2024, 12, 31)]
    assert start == date(2024, 1, 1)
    assert value == 600.0


# ---------------------------------------------------------------- the load loop

def test_ingest_writes_rows_and_derives_fcf(client, monkeypatch):
    """free_cash_flow = operating cash flow - capex, and only when both exist."""
    from models import Financials

    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 300.0, frame="CY2024Q1"),
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 100.0, frame="CY2024Q1"),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 50.0, frame="CY2024Q1"),
        ]}},
    }}}

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: facts)

    written = ingest_module.ingest_company("MSFT")
    assert written == 1

    db = TestingSessionLocal()
    try:
        row = db.query(Financials).one()
        assert row.period_end == date(2024, 3, 31)
        assert float(row.free_cash_flow) == 200.0     # 300 - 100
        assert float(row.net_income) == 50.0
        assert row.revenue is None                    # absent stays absent
    finally:
        db.close()


def test_ingest_leaves_fcf_null_when_capex_is_missing(client, monkeypatch):
    """Unknown is not zero: OCF alone must not be reported as free cash flow."""
    from models import Financials

    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 300.0, frame="CY2024Q1"),
        ]}},
    }}}

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: facts)
    ingest_module.ingest_company("MSFT")

    db = TestingSessionLocal()
    try:
        assert db.query(Financials).one().free_cash_flow is None
    finally:
        db.close()


def test_ingest_sums_current_and_noncurrent_debt(client, monkeypatch):
    from models import Financials

    facts = {"facts": {"us-gaap": {
        "LongTermDebtCurrent": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 10.0, frame="CY2024Q1I"),
        ]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 90.0, frame="CY2024Q1I"),
        ]}},
    }}}

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: facts)
    ingest_module.ingest_company("MSFT")

    db = TestingSessionLocal()
    try:
        assert float(db.query(Financials).one().total_debt) == 100.0
    finally:
        db.close()


def test_ingest_total_debt_null_when_neither_component_exists(client, monkeypatch):
    from models import Financials

    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 50.0, frame="CY2024Q1"),
        ]}},
    }}}

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: facts)
    ingest_module.ingest_company("MSFT")

    db = TestingSessionLocal()
    try:
        assert db.query(Financials).one().total_debt is None
    finally:
        db.close()


def test_ingest_is_idempotent_on_rerun(client, monkeypatch):
    """The docstring promises reruns write no new rows."""
    from models import Financials

    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            _entry("2024-01-01", "2024-03-31", 50.0, frame="CY2024Q1"),
        ]}},
    }}}

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: facts)

    ingest_module.ingest_company("MSFT")
    ingest_module.ingest_company("MSFT")

    db = TestingSessionLocal()
    try:
        assert db.query(Financials).count() == 1
    finally:
        db.close()


def test_ingest_rerun_updates_a_restated_figure(client, monkeypatch):
    """Companies restate. An upsert must carry the new value through."""
    from models import Financials

    def _facts_with(value):
        return {"facts": {"us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [
                _entry("2024-01-01", "2024-03-31", value, frame="CY2024Q1"),
            ]}},
        }}}

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))

    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: _facts_with(50.0))
    ingest_module.ingest_company("MSFT")
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: _facts_with(75.0))
    ingest_module.ingest_company("MSFT")

    db = TestingSessionLocal()
    try:
        row = db.query(Financials).one()
        assert float(row.net_income) == 75.0
    finally:
        db.close()


# --- the fetch/store split ---
#
# fetch_financials is network and arithmetic only: no session, no shared
# mutable state, so it can run in a worker thread alongside the filing and
# price fetches. store_financials is the database half. ingest_company
# composes them and is unchanged from the caller's point of view.

from ingest import _row_values, fetch_financials, store_financials


def test_fetch_financials_touches_no_database(monkeypatch):
    """If it needed a session it could not be parallelised."""
    monkeypatch.setattr(ingest_module, "get_cik", lambda t: ("789019", "Microsoft"))
    monkeypatch.setattr(ingest_module, "fetch_company_facts", lambda cik: _facts([
        _entry("2024-01-01", "2024-03-31", 50.0, frame="CY2024Q1"),
    ], tag="NetIncomeLoss"))

    def _explode():
        raise AssertionError("fetch_financials opened a database session")

    monkeypatch.setattr(ingest_module, "SessionLocal", _explode)
    cik, name, series = fetch_financials("msft")
    assert cik == "789019"
    assert name == "Microsoft"
    assert series["net_income"][date(2024, 3, 31)] == 50.0


def test_fetch_financials_normalizes_the_ticker(monkeypatch):
    seen = []
    monkeypatch.setattr(ingest_module, "get_cik",
                        lambda t: (seen.append(t), ("789019", "Microsoft"))[1])
    monkeypatch.setattr(ingest_module, "fetch_company_facts",
                        lambda cik: {"facts": {"us-gaap": {}}})
    fetch_financials("msft")
    assert seen == ["MSFT"]


def test_store_financials_writes_the_rows(client, monkeypatch):
    from models import Financials

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    series = {k: {} for k in ("revenue", "net_income", "operating_cash_flow",
                              "capex", "equity", "debt_current",
                              "debt_noncurrent", "cash",
                              "short_term_investments")}
    series["net_income"] = {date(2024, 3, 31): 50.0, date(2024, 6, 30): 60.0}

    written = store_financials("MSFT", "Microsoft", series)
    assert written == 2

    db = TestingSessionLocal()
    try:
        assert db.query(Financials).count() == 2
    finally:
        db.close()


def test_store_financials_with_no_periods_writes_nothing(client, monkeypatch):
    from models import Financials

    monkeypatch.setattr(ingest_module, "SessionLocal", TestingSessionLocal)
    series = {k: {} for k in ("revenue", "net_income", "operating_cash_flow",
                              "capex", "equity", "debt_current",
                              "debt_noncurrent", "cash",
                              "short_term_investments")}
    assert store_financials("MSFT", "Microsoft", series) == 0

    db = TestingSessionLocal()
    try:
        assert db.query(Financials).count() == 0
    finally:
        db.close()


# --- _row_values keeps the unknown-is-not-zero rule ---

def _series_with(**overrides):
    base = {k: {} for k in ("revenue", "net_income", "operating_cash_flow",
                            "capex", "equity", "debt_current",
                            "debt_noncurrent", "cash", "short_term_investments")}
    period = date(2024, 3, 31)
    for key, value in overrides.items():
        base[key] = {period: value}
    return base, period


def test_row_values_derives_free_cash_flow():
    series, period = _series_with(operating_cash_flow=300.0, capex=100.0)
    assert _row_values(1, period, series)["free_cash_flow"] == 200.0


def test_row_values_leaves_fcf_null_without_capex():
    series, period = _series_with(operating_cash_flow=300.0)
    assert _row_values(1, period, series)["free_cash_flow"] is None


def test_row_values_sums_debt_components():
    series, period = _series_with(debt_current=10.0, debt_noncurrent=90.0)
    assert _row_values(1, period, series)["total_debt"] == 100.0


def test_row_values_accepts_one_debt_component():
    series, period = _series_with(debt_noncurrent=90.0)
    assert _row_values(1, period, series)["total_debt"] == 90.0


def test_row_values_leaves_debt_null_when_both_absent():
    series, period = _series_with(revenue=1.0)
    assert _row_values(1, period, series)["total_debt"] is None
