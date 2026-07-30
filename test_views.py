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
from views import money, pct, mult, num

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
