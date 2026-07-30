"""Formatters must survive whatever the cache hands back.

The report payload is serialized to JSON on the cache write and parsed
back on the read. That round trip is a type boundary: Decimals from the
Numeric columns become floats, dates become strings. An older cache row
written with json.dumps(default=str) even stored the numbers as strings.
Every formatter has to coerce defensively rather than trust the incoming
type — mult() learned this the hard way, then pct() crashed identically a
day later on a cached string, then money() would have crashed on a
negative one. This file pins the whole family to the same contract:

    None -> em-dash;  otherwise float(v) then format.
"""

import json
from datetime import date
from decimal import Decimal

from main import to_jsonable
from views import money, pct, mult, num, render_report

EM_DASH = "—"
FORMATTERS = (money, pct, mult, num)


def _sample_report() -> dict:
    """A report shaped like the real thing: Decimals and dates, as they
    come off the Numeric columns before serialization."""
    return {
        "as_of": date(2025, 6, 30),
        "ttm": {
            "revenue": Decimal("318273000000"),
            "net_income": Decimal("125216000000"),
            "net_margin": Decimal("0.393"),
            "roic": Decimal("0.275"),
        },
        "valuation": {
            "market_cap": Decimal("2500000000000"),
            "p_fcf": Decimal("40.5"),
            "p_e": Decimal("23.4"),
        },
    }


def test_roundtrip_through_cache_boundary():
    """json.dumps(default=to_jsonable) -> json.loads, then every formatter
    must handle the parsed values without raising."""
    restored = json.loads(json.dumps(_sample_report(), default=to_jsonable))

    ttm = restored["ttm"]
    val = restored["valuation"]

    assert money(ttm["revenue"]) == "$318.3B"
    assert money(ttm["net_income"]) == "$125.2B"
    assert money(val["market_cap"]) == "$2.50T"
    assert pct(ttm["net_margin"]) == "39.3%"
    assert pct(ttm["roic"]) == "27.5%"
    assert mult(val["p_fcf"]) == "40.5x"
    assert num(val["p_e"]) == "23.40"


def test_every_formatter_handles_none():
    for f in FORMATTERS:
        assert f(None) == EM_DASH


def test_every_formatter_handles_decimal():
    # float(Decimal(...)) is exact enough here; the point is that a raw
    # Decimal off a Numeric column never reaches a formatter unconverted.
    assert money(Decimal("1200000000")) == "$1.2B"
    assert pct(Decimal("0.42")) == "42.0%"
    assert mult(Decimal("23.4")) == "23.4x"
    assert num(Decimal("12.5")) == "12.50"


def test_every_formatter_handles_stringified_numbers():
    # The historical crash: a cache written with json.dumps(default=str)
    # stored numbers as strings. money() also has to survive a NEGATIVE
    # string, which the pre-fix "-12" < 0 comparison could not.
    assert money("-1200000000") == "-$1.2B"
    assert pct("0.42") == "42.0%"
    assert mult("23.4") == "23.4x"
    assert num("12.5") == "12.50"


def _legacy_stringified_report() -> dict:
    """A report as an OLD json.dumps(default=str) cache row would hand it
    back: every number is a string. render_report does its own comparisons
    and formatting in _health, _figures and the footer, so those sites must
    coerce just like the formatters do — a bad one 500s the tearsheet."""
    return {
        "company": "MSFT",
        "name": "Microsoft Corp",
        "data": {
            "as_of": "2025-06-30",
            "ttm": {"revenue": "318273000000", "net_income": "125216000000",
                    "net_margin": "0.393", "fcf_margin": "0.229",
                    "revenue_growth": "0.179", "roic": "0.275", "roe": "0.302"},
            "price": {"price": "512.30", "market_cap": "3700000000000"},
            "scorecard": {
                "checks": [{"name": "ROIC", "status": "PASS", "detail": "27.5% vs 15%"}],
                "summary": {"passed": 5, "evaluable": 6, "unknown": 0},
                "valuation": {"market_cap": "3700000000000", "p_fcf": "40.5", "p_e": "23.4"},
                "financial_health": {
                    # change as a string is the exact value that crashed `> 0`
                    "cash": {"prior": "70000000000", "current": "75000000000",
                             "change": "5000000000"},
                    "total_debt": {"prior": "45000000000", "current": "42000000000",
                                   "change": "-3000000000"},
                    "survivability": {"verdict": "Comfortably survivable."},
                },
            },
        },
        "narrative": {"verdict": "WATCH-CASE", "grounding_rate": "1.0",
                      "hype_vs_reality": "x", "risks": [], "reasoning": "y",
                      "strategy": "z"},
        "sources": {"financials": "SEC EDGAR", "price": "yfinance",
                    "filing": "http://x", "report_date": "2025-07-30"},
    }


def test_render_report_survives_legacy_stringified_cache():
    html = render_report(_legacy_stringified_report())
    assert html.strip().startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    # The values that used to crash now render.
    assert "WATCH-CASE" in html
    assert "100%" in html          # grounding_rate coerced from "1.0"
    assert "$512.30" in html       # share price coerced from "512.30"
    assert "$5.0B" in html         # a positive change coerced from a string
    assert "-$3.0B" in html        # a negative change too
