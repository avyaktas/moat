"""Tests for report data assembly.

Only the deterministic half is tested here - build_report_data takes rows
and a price dict and returns computed figures. The synthesis step is an
LLM call, tested separately via mocking at the endpoint level.

A tiny stand-in class replaces the ORM row so these tests need no database.
"""

from dataclasses import dataclass
from datetime import date

from report import _growth, _row_to_dict, build_report_data


@dataclass
class FakeRow:
    """Stands in for a Financials ORM row."""
    period_end: date
    revenue: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    total_debt: float | None = None
    shareholders_equity: float | None = None
    cash: float | None = None
    short_term_investments: float | None = None


def make_rows(n: int = 8, revenue: float = 100.0, income: float = 30.0,
              fcf: float = 25.0) -> list[FakeRow]:
    """n quarters, newest first, with steady figures."""
    return [
        FakeRow(
            period_end=date(2026, 3, 31),
            revenue=revenue,
            net_income=income,
            free_cash_flow=fcf,
            total_debt=40.0,
            shareholders_equity=400.0,
            cash=32.0,
            short_term_investments=46.0,
        )
        for _ in range(n)
    ]


# --- growth helper ---

def test_growth_computes_rate():
    assert _growth(110.0, 100.0) == 0.10


def test_growth_none_when_missing():
    assert _growth(None, 100.0) is None


def test_growth_none_on_nonpositive_base():
    # Growth off a negative or zero base is not interpretable.
    assert _growth(110.0, 0.0) is None
    assert _growth(110.0, -50.0) is None


# --- row conversion ---

def test_row_to_dict_extracts_fields():
    row = FakeRow(period_end=date(2026, 3, 31), revenue=100.0, cash=32.0)
    d = _row_to_dict(row)
    assert d["revenue"] == 100.0
    assert d["cash"] == 32.0


def test_row_to_dict_handles_none():
    assert _row_to_dict(None) == {}


# --- report assembly ---

def test_report_errors_without_data():
    assert "error" in build_report_data([], None)


def test_report_computes_ttm():
    rows = make_rows(8)
    data = build_report_data(rows, None)
    assert data["ttm"]["revenue"] == 400.0       # 4 quarters x 100
    assert data["ttm"]["net_income"] == 120.0    # 4 x 30
    assert data["ttm"]["free_cash_flow"] == 100.0


def test_report_computes_margins():
    data = build_report_data(make_rows(8), None)
    assert data["ttm"]["net_margin"] == 0.30
    assert data["ttm"]["fcf_margin"] == 0.25


def test_report_growth_zero_when_flat():
    # Eight identical quarters means no year-over-year change.
    data = build_report_data(make_rows(8), None)
    assert data["ttm"]["revenue_growth"] == 0.0


def test_report_growth_none_with_too_few_quarters():
    # Fewer than 8 quarters means no prior-year TTM to compare against.
    data = build_report_data(make_rows(4), None)
    assert data["ttm"]["revenue_growth"] is None


def test_report_includes_scorecard():
    data = build_report_data(make_rows(8), None)
    assert "scorecard" in data
    assert "checks" in data["scorecard"]
    assert len(data["scorecard"]["checks"]) == 6


def test_report_valuation_needs_price():
    without = build_report_data(make_rows(8), None)
    assert without["scorecard"]["valuation"]["p_fcf"] is None

    with_price = build_report_data(
        make_rows(8), {"price": 50.0, "market_cap": 1000.0}
    )
    assert with_price["scorecard"]["valuation"]["p_fcf"] == 10.0  # 1000 / 100


def test_report_handles_missing_metrics_without_crashing():
    rows = [FakeRow(period_end=date(2026, 3, 31)) for _ in range(8)]
    data = build_report_data(rows, None)
    assert data["ttm"]["revenue"] is None
    assert data["ttm"]["net_margin"] is None
    # Every check should be UNKNOWN, not FAIL.
    statuses = {c["status"] for c in data["scorecard"]["checks"]}
    assert statuses == {"UNKNOWN"}


def test_report_partial_data_yields_mixed_statuses():
    rows = make_rows(8)
    # Wipe FCF so that check becomes unevaluable while others still resolve.
    for r in rows:
        r.free_cash_flow = None
    data = build_report_data(rows, None)
    statuses = [c["status"] for c in data["scorecard"]["checks"]]
    assert "UNKNOWN" in statuses
    assert "PASS" in statuses or "FAIL" in statuses