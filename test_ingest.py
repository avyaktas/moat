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
    ytd = dict((end, val) for _, end, val in extract_ytd(_facts(CUMULATIVE), ["OCF"]))
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
