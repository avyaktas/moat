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
from views import money, mult, num, pct, render_report

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


# --- unknown is not zero, on the page as well as in the data ---
#
# The project's load-bearing rule is that missing data stays missing and is
# never conflated with zero. _health used `if change:` to decide whether to
# render a change, so a genuine zero took the same em-dash as an unknown -
# the rule held all the way through the pipeline and then broke in the last
# place anyone would check, the rendered page.

def _report_with_health(health: dict) -> dict:
    return {
        "company": "TEST", "name": "Test Co",
        "data": {
            "as_of": "2025-06-30",
            "ttm": {},
            "price": None,
            "scorecard": {
                "checks": [], "summary": {"passed": 0, "evaluable": 0, "unknown": 0},
                "valuation": {},
                "financial_health": health,
            },
        },
        "narrative": None, "sources": {}, "cache": {},
    }


def test_zero_change_renders_as_zero_not_unknown():
    html = render_report(_report_with_health({
        "cash": {"prior": 1000.0, "current": 1000.0, "change": 0.0},
        "survivability": {"verdict": ""},
    }))
    row = html.split("Cash")[1].split("</tr>")[0]
    assert "$0" in row, "a real zero change must render as $0"


def test_unknown_change_still_renders_as_em_dash():
    html = render_report(_report_with_health({
        "cash": {"prior": None, "current": 1000.0, "change": None},
        "survivability": {"verdict": ""},
    }))
    row = html.split("Cash")[1].split("</tr>")[0]
    assert EM_DASH in row, "an unknown change must stay an em-dash"
    assert "$0" not in row


def test_zero_and_unknown_change_render_differently():
    """The whole point: these two must not look the same."""
    zero = render_report(_report_with_health({
        "cash": {"prior": 1.0, "current": 1.0, "change": 0.0},
        "survivability": {"verdict": ""},
    })).split("Cash")[1].split("</tr>")[0]
    unknown = render_report(_report_with_health({
        "cash": {"prior": None, "current": None, "change": None},
        "survivability": {"verdict": ""},
    })).split("Cash")[1].split("</tr>")[0]
    assert zero != unknown


def _report_with_checks(checks: list) -> dict:
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["data"]["scorecard"]["checks"] = checks
    return r


def test_unexpected_check_status_does_not_crash():
    """A status outside the three known values used to raise KeyError."""
    html = render_report(_report_with_checks(
        [{"name": "Novel", "status": "SKIPPED", "detail": "new status"}]
    ))
    assert "Novel" in html


def test_missing_filing_url_is_not_a_dead_link():
    """esc(None) is the empty string, so href="" linked to the current page."""
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["sources"] = {"financials": "SEC EDGAR", "price": "yfinance",
                    "filing": None, "report_date": None}
    html = render_report(r)
    assert 'href=""' not in html


def test_present_filing_url_is_still_a_link():
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["sources"] = {"financials": "SEC EDGAR", "price": "yfinance",
                    "filing": "https://example.com/10k.htm",
                    "report_date": "2025-06-30"}
    html = render_report(r)
    assert 'href="https://example.com/10k.htm"' in html


def test_zero_share_price_renders_as_zero():
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["data"]["price"] = {"price": 0}
    html = render_report(r)
    assert "$0.00" in html


# --- the footer timestamp must actually be UTC ---
#
# views renders cache.generated_at as `value[:19] + " UTC"`. Postgres returns
# timestamptz in the session timezone, so a cached report handed back
# "2026-09-24T14:44:35-04:00" was displayed as "2026-09-24 14:44:35 UTC" -
# four hours wrong, with a label asserting it was not.

def test_footer_timestamp_is_labelled_utc_only_when_it_is_utc():
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["cache"] = {"cached": True, "generated_at": "2026-09-24T18:44:35+00:00"}
    html = render_report(r)
    assert "2026-09-24 18:44:35 UTC" in html


def test_footer_does_not_mislabel_a_local_timestamp_as_utc():
    """If a non-UTC offset ever reaches the renderer, it must not be stamped
    UTC. The payload should never contain one - this is the backstop."""
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["cache"] = {"cached": True, "generated_at": "2026-09-24T14:44:35-04:00"}
    html = render_report(r)
    assert "2026-09-24 14:44:35 UTC" not in html, (
        "a local-time value was rendered with a UTC label"
    )


# --- the landing page and the 404 page ---
#
# Both are user-facing HTML that nothing rendered in a test. A crash in either
# is a blank page at the front door.

from views import render_landing, render_not_found


def test_landing_page_is_a_complete_document():
    html = render_landing()
    assert html.strip().startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert "<title>" in html


def test_landing_page_offers_a_way_in():
    html = render_landing()
    assert "/report/view" in html
    assert "<form" in html


def test_landing_page_carries_the_disclaimer():
    """The claim that this is not investment advice is not optional."""
    assert "not investment advice" in render_landing()


def test_not_found_page_is_a_complete_document():
    html = render_not_found("Unknown ticker: ZZZZ")
    assert html.strip().startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_not_found_page_shows_the_detail_and_a_way_back():
    html = render_not_found("Unknown ticker: ZZZZ")
    assert "Unknown ticker: ZZZZ" in html
    assert 'href="/"' in html


def test_not_found_page_escapes_the_detail():
    """The detail contains a user-supplied ticker and is interpolated into
    HTML. Unescaped, that is reflected XSS on the 404 page."""
    html = render_not_found('<script>alert("xss")</script>')
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_report_escapes_a_hostile_company_name():
    r = _report_with_health({"survivability": {"verdict": ""}})
    r["name"] = '<img src=x onerror="alert(1)">'
    html = render_report(r)
    assert "<img src=x" not in html
    assert "&lt;img" in html


# --- the progressive report ---
#
# A cold ticker takes about thirty seconds, nearly all of it the model
# writing. The page used to be a blank document for that whole time. These
# cover the three states it can now be in.

from views import (
    render_failure,
    render_report_fragment,
    render_report_shell,
)


def test_shell_is_a_complete_document():
    html = render_report_shell("NVDA")
    assert html.strip().startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_shell_shows_the_ticker_immediately():
    """Something real in the first response, not after JavaScript runs."""
    assert "NVDA" in render_report_shell("nvda")


def test_shell_lists_every_stage_server_side():
    html = render_report_shell("NVDA")
    for key in ("fetch", "store", "metrics", "synthesis"):
        assert f'data-stage="{key}"' in html
    assert "Fetching SEC filings" in html
    assert "Writing analysis" in html


def test_shell_starts_with_the_first_stage_running():
    html = render_report_shell("NVDA")
    assert 'data-stage="fetch" data-state="running"' in html


def test_shell_connects_to_the_stream_for_that_ticker():
    assert "/company/NVDA/report/stream" in render_report_shell("nvda")


def test_shell_escapes_the_ticker():
    """The ticker reaches the page from the URL."""
    html = render_report_shell('X"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in html


def test_shell_offers_a_path_without_javascript():
    html = render_report_shell("NVDA")
    assert "<noscript>" in html
    assert "/company/NVDA/report" in html


def test_shell_handles_a_dropped_connection():
    """A stream that dies must not leave the page spinning forever."""
    html = render_report_shell("NVDA")
    assert "onerror" in html
    assert "Connection lost" in html


def _computed_only_report() -> dict:
    r = _report_with_health({
        "cash": {"prior": 1.0, "current": 2.0, "change": 1.0},
        "survivability": {"verdict": "Self-funding"},
    })
    r["data"]["ttm"] = {"revenue": 331839000000.0, "roic": 0.277}
    r["data"]["scorecard"]["checks"] = [
        {"name": "ROIC", "status": "PASS", "detail": "27.7% vs 15%"},
        {"name": "Leverage", "status": "FAIL", "detail": "2.4 - a red flag"},
    ]
    r["data"]["scorecard"]["summary"] = {"passed": 1, "evaluable": 2, "unknown": 0}
    r["sources"] = {"financials": "SEC EDGAR", "price": "yfinance",
                    "filing": "http://x", "report_date": "2026-01-31"}
    return r


def test_partial_fragment_shows_the_computed_figures():
    """The figures are final before the model starts - that is the point."""
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert "$331.8B" in html
    assert "27.7%" in html
    assert "ROIC" in html
    assert "Self-funding" in html


def test_partial_fragment_marks_the_narrative_as_still_being_written():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert "Writing analysis" in html
    assert "pending" in html


def test_partial_fragment_verdict_reads_pending_not_no_verdict():
    """NO VERDICT means the framework reached one. This has not run yet."""
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert "PENDING" in html
    assert "NO VERDICT" not in html


def test_partial_fragment_omits_the_grounding_claim():
    """No quotes exist yet, so claiming a verification rate would be a lie."""
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert "of quotes verified" not in html


def test_finished_fragment_has_no_pending_markers():
    r = _computed_only_report()
    r["narrative"] = {"verdict": "WATCH-CASE", "grounding_rate": 1.0,
                      "hype_vs_reality": "h", "risks": [], "reasoning": "r",
                      "strategy": "s"}
    html = render_report_fragment(r, pending=False)
    assert "Writing analysis" not in html
    assert "WATCH-CASE" in html
    assert "of quotes verified" in html


def test_partial_and_final_share_one_layout():
    """Two copies of the layout would drift; there is only one builder."""
    r = _computed_only_report()
    partial = render_report_fragment(r, pending=True)
    r["narrative"] = {"verdict": "WATCH-CASE", "grounding_rate": 1.0,
                      "hype_vs_reality": "h", "risks": [], "reasoning": "r",
                      "strategy": "s"}
    final = render_report_fragment(r, pending=False)
    for heading in ("Scorecard", "Figures", "Financial health",
                    "Hype versus reality", "Risks and sell triggers",
                    "The case", "The strategy"):
        assert heading in partial, f"{heading} missing while pending"
        assert heading in final


def test_failure_block_shows_the_reason_and_a_way_back():
    html = render_failure("SEC EDGAR unavailable", "Try again shortly.")
    assert "SEC EDGAR unavailable" in html
    assert "Try again shortly." in html
    assert 'href="/"' in html


def test_failure_block_escapes_its_input():
    html = render_failure("<script>x</script>", "<img src=x onerror=y>")
    assert "<script>x</script>" not in html
    assert "<img src=x" not in html
