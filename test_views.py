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


# --- the landing page acknowledges a submit immediately ---

def test_landing_page_acknowledges_the_submit():
    """A button that does nothing visible when pressed is the whole complaint."""
    html = render_landing()
    assert "Loading " in html
    assert "btn.disabled = true" in html


def test_landing_page_has_a_live_region_for_the_status():
    html = render_landing()
    assert 'aria-live="polite"' in html


def test_pending_labels_render_as_text_not_entities():
    """_pending escapes its argument, so an HTML entity passed in shows up
    literally. Observed in the browser as "WRITING ANALYSIS&HELLIP;"."""
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert "&HELLIP;" not in html.upper()
    assert "&amp;hellip;" not in html
    assert "Writing analysis…" in html


def test_every_sheet_rendering_keeps_the_swap_anchor():
    """The page replaces this element as each stage lands, so the id has to
    survive the swap.

    Without it the partial replaced the only element carrying id="sheet", and
    the done handler threw "Cannot set properties of null" - leaving the page
    on PENDING forever even though the report had been built and cached.
    Found in a browser; no fragment test in isolation could have caught it.
    """
    report = _computed_only_report()
    assert 'id="sheet"' in render_report_shell("NVDA")
    assert 'id="sheet"' in render_report_fragment(report, pending=True)
    report["narrative"] = {"verdict": "WATCH-CASE", "grounding_rate": 1.0,
                           "hype_vs_reality": "h", "risks": [], "reasoning": "r",
                           "strategy": "s"}
    assert 'id="sheet"' in render_report_fragment(report, pending=False)
    assert 'id="sheet"' in render_report(report)


def test_the_swap_anchor_is_unique_per_rendering():
    """Two elements with the same id would make the swap pick one at random."""
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert html.count('id="sheet"') == 1


# --- the design system ---
#
# Tokens, not literals. Every page pulls the same palette and spacing scale
# from one place, so light and dark are a variable swap rather than a second
# stylesheet, and a colour can only be wrong in one spot.

from views import _TOKENS


def test_both_themes_are_defined():
    assert "prefers-color-scheme: dark" in _TOKENS
    assert '[data-theme="dark"]' in _TOKENS
    assert '[data-theme="light"]' in _TOKENS


def test_the_palette_is_variables_not_literals():
    for token in ("--bg", "--surface", "--border", "--text", "--text-muted",
                  "--accent", "--pos", "--neg"):
        assert f"{token}:" in _TOKENS, f"{token} missing"


def test_there_is_a_spacing_scale():
    for step in ("--s1", "--s2", "--s4", "--s6", "--s8"):
        assert f"{step}:" in _TOKENS


def test_numbers_use_tabular_figures():
    """Otherwise digits change width as values update and columns jitter."""
    assert "tabular-nums" in _TOKENS
    assert "'tnum'" in _TOKENS


def test_focus_is_visible():
    """Keyboard users need to see where they are, in both themes."""
    assert ":focus-visible" in _TOKENS
    assert "outline:" in _TOKENS


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion: reduce" in _TOKENS


def test_one_typeface_with_a_system_fallback():
    from views import _FONTS

    assert "Inter" in _FONTS
    assert "display=swap" in _FONTS, "text must not be invisible while loading"
    assert 'rel="preload"' in _FONTS
    assert "-apple-system" in _TOKENS, "no system fallback for the webfont"


def test_the_old_display_faces_are_gone():
    """Three families was three loads and three chances to flash."""
    from views import _FONTS

    assert "Instrument+Serif" not in _FONTS
    assert "JetBrains" not in _FONTS


def test_every_page_carries_the_tokens():
    assert "--accent" in render_landing()
    assert "--accent" in render_not_found("x")
    assert "--accent" in render_report_shell("NVDA")


# --- search behaviour ---

def test_search_autofocuses():
    assert "inp.focus();" in render_landing()


def test_slash_focuses_search():
    """The shortcut every search-first product has."""
    html = render_landing()
    assert "keydown" in html
    assert "e.key !== '/'" in html


def test_slash_does_not_steal_keystrokes_while_typing():
    html = render_landing()
    assert "TEXTAREA" in html
    assert "isContentEditable" in html


def test_recent_searches_are_remembered():
    html = render_landing()
    assert "moat.recent" in html
    assert "localStorage" in html


def test_recent_searches_survive_private_mode():
    """localStorage throws in some browsers; a search must not fail for it."""
    html = render_landing()
    assert "catch (err)" in html


def test_recent_list_is_bounded():
    assert "slice(0, 5)" in render_landing()


def test_recent_row_is_hidden_until_there_is_something_in_it():
    html = render_landing()
    assert 'id="recent-row" hidden' in html


def test_example_tickers_are_still_offered():
    html = render_landing()
    for ticker in ("MSFT", "AAPL", "NVDA", "IBM"):
        assert f"/company/{ticker}/report/view" in html


# --- the report layout ---

def test_verdict_renders_as_a_colour_coded_badge():
    r = _computed_only_report()
    for verdict, cls in (("BUY-CASE", "buy"), ("WATCH-CASE", "watch"),
                         ("AVOID-CASE", "avoid")):
        r["narrative"] = {"verdict": verdict, "grounding_rate": 1.0,
                          "hype_vs_reality": "h", "risks": [], "reasoning": "r",
                          "strategy": "s"}
        html = render_report_fragment(r, pending=False)
        assert f'class="badge {cls}"' in html, f"{verdict} badge missing"


def test_an_unknown_verdict_gets_the_neutral_badge():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert 'class="badge none"' in html


def test_negative_changes_are_red_and_positive_green():
    """Direction is the point of the change column."""
    html = render_report_fragment(_report_with_health({
        "cash": {"prior": 10.0, "current": 5.0, "change": -5_000_000_000.0},
        "total_debt": {"prior": 5.0, "current": 10.0, "change": 5_000_000_000.0},
        "survivability": {"verdict": ""},
    }), pending=True)
    assert '<span class="neg">-$5.0B</span>' in html
    assert '<span class="pos">$5.0B</span>' in html


def test_an_unknown_change_gets_no_colour():
    """Missing is not a direction."""
    html = render_report_fragment(_report_with_health({
        "cash": {"prior": None, "current": None, "change": None},
        "survivability": {"verdict": ""},
    }), pending=True)
    row = html.split("Cash")[1].split("</tr>")[0]
    assert "pos" not in row and "neg" not in row
    assert EM_DASH in row


def test_a_zero_change_gets_no_colour():
    html = render_report_fragment(_report_with_health({
        "cash": {"prior": 1.0, "current": 1.0, "change": 0.0},
        "survivability": {"verdict": ""},
    }), pending=True)
    row = html.split("Cash")[1].split("</tr>")[0]
    assert "pos" not in row and "neg" not in row


def test_the_meter_has_one_segment_per_criterion():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert html.count('class="seg ') == 2   # the sample report has two checks


def test_the_meter_is_described_for_screen_readers():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert 'role="img"' in html
    assert "criteria hold" in html


def test_the_scorecard_is_a_grid_of_cards():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert 'class="checks"' in html
    assert 'class="check pass"' in html
    assert 'class="check fail"' in html


def test_check_cards_carry_a_status_tag():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert 'class="tag pass"' in html
    assert 'class="tag fail"' in html


def test_number_columns_are_marked_for_alignment():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert 'class="n"' in html


def test_health_rows_use_header_cells_for_their_labels():
    """A data table's row labels are headers; screen readers announce them."""
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert '<th scope="row">Cash</th>' in html
    assert 'scope="col"' in html


def test_the_health_table_can_scroll_on_a_narrow_screen():
    html = render_report_fragment(_computed_only_report(), pending=True)
    assert 'class="table-scroll"' in html


def test_the_verified_badge_is_visible_on_a_quote():
    r = _computed_only_report()
    r["narrative"] = {
        "verdict": "WATCH-CASE", "grounding_rate": 1.0, "hype_vs_reality": "h",
        "reasoning": "r", "strategy": "s",
        "risks": [{"risk": "R", "quote": "Q", "sell_trigger": "T",
                   "quote_verified": True}],
    }
    html = render_report_fragment(r, pending=False)
    assert 'class="verified"' in html
    assert "Quote verified against filing" in html
    assert "<blockquote>" in html


def test_an_unverified_quote_is_marked_differently():
    r = _computed_only_report()
    r["narrative"] = {
        "verdict": "WATCH-CASE", "grounding_rate": 0.0, "hype_vs_reality": "h",
        "reasoning": "r", "strategy": "s",
        "risks": [{"risk": "R", "quote": "Q", "sell_trigger": "T",
                   "quote_verified": False}],
    }
    html = render_report_fragment(r, pending=False)
    assert 'class="unverified"' in html
    assert "Quote not found in filing" in html


def test_the_report_has_a_sticky_bar_with_a_theme_toggle():
    r = _computed_only_report()
    r["narrative"] = {"verdict": "BUY-CASE", "grounding_rate": 1.0,
                      "hype_vs_reality": "h", "risks": [], "reasoning": "r",
                      "strategy": "s"}
    html = render_report(r)
    assert 'class="topbar"' in html
    assert 'id="theme"' in html
    assert "moat.theme" in html


def test_the_theme_choice_is_remembered():
    r = _computed_only_report()
    r["narrative"] = {"verdict": "BUY-CASE", "grounding_rate": 1.0,
                      "hype_vs_reality": "h", "risks": [], "reasoning": "r",
                      "strategy": "s"}
    html = render_report(r)
    assert "localStorage" in html
    assert "catch (err)" in html, "private mode must not break the page"


# --- zero layout shift ---
#
# A generic spinner tells you to wait. A skeleton tells you what is coming and
# holds its seat, so when the figures land they land in place: the swap
# changes pixels, not positions.

def test_the_skeleton_has_the_same_shape_as_the_report():
    """Six criteria and twelve figures are fixed by the domain, so the
    placeholder can reserve exactly the right number of boxes."""
    shell = render_report_shell("NVDA")
    assert shell.count('class="check"') == 6
    assert shell.count('class="fig"') == 12


def test_the_skeleton_uses_the_same_classes_as_the_real_content():
    """Same classes means the same CSS box, which is what makes the swap
    invisible. Different markup would need its sizes kept in sync by hand."""
    shell = render_report_shell("NVDA")
    real = render_report_fragment(_computed_only_report(), pending=True)
    for cls in ('class="sheet"', 'class="hero"', 'class="checks"',
                'class="figures"', 'class="meter"', 'class="table-scroll"'):
        assert cls in shell, f"{cls} missing from the skeleton"
        assert cls in real, f"{cls} missing from the report"


def test_the_skeleton_reserves_every_section():
    shell = render_report_shell("NVDA")
    for heading in ("Scorecard", "Figures", "Financial health",
                    "Hype versus reality", "Risks and sell triggers",
                    "The case", "The strategy"):
        assert heading in shell, f"{heading} not reserved while loading"


def test_the_skeleton_reserves_five_health_rows():
    assert render_report_shell("NVDA").count('<th scope="row">') == 5


def test_repeated_blocks_have_a_fixed_height():
    """Without this the skeleton and the filled card are different sizes and
    the page jumps on arrival - the whole point of the exercise."""
    from views import _REPORT_CSS

    # Measured in a browser, skeleton against filled, not guessed.
    assert "min-height: 108px" in _REPORT_CSS   # criterion card
    assert "min-height: 90px" in _REPORT_CSS    # figure tile
    assert "height: 45px" in _REPORT_CSS        # health row
    assert "min-height: 48px" in _REPORT_CSS    # survivability panel
    assert "min-height: 26px" in _REPORT_CSS    # share price
    assert "min-height: 23px" in _REPORT_CSS    # hero subtitle
    assert "min-height: 21px" in _REPORT_CSS    # meter caption


def test_content_fades_in_when_it_lands():
    from views import _REPORT_CSS

    assert "@keyframes landed" in _REPORT_CSS
    assert "animation: landed 180ms" in _REPORT_CSS
    assert "classList.add('landed')" in render_report_shell("NVDA")


def test_the_fade_does_not_start_from_blank():
    """Fading from zero would flash an empty page between states."""
    from views import _REPORT_CSS

    keyframes = _REPORT_CSS.split("@keyframes landed")[1].split("}")[0]
    assert "opacity: 0.4" in keyframes
    assert "opacity: 0;" not in keyframes


def test_progress_is_out_of_flow():
    """Floating, so it can arrive and leave without moving the report."""
    from views import _REPORT_CSS

    progress = _REPORT_CSS.split(".progress {")[1].split("}")[0]
    assert "position: fixed" in progress


def test_progress_announces_itself_to_screen_readers():
    shell = render_report_shell("NVDA")
    assert 'aria-live="polite"' in shell
    assert 'aria-label="Report progress"' in shell


def test_progress_is_dismissed_after_the_report_lands():
    shell = render_report_shell("NVDA")
    assert "dismiss(" in shell
    assert "gone" in shell


def test_the_ticker_is_known_immediately():
    """It comes from the URL, so it never needs a placeholder."""
    shell = render_report_shell("nvda")
    assert "<h1>NVDA</h1>" in shell


def test_skeleton_placeholders_are_marked():
    assert 'class="sk ' in render_report_shell("NVDA")


def test_the_skeleton_reserves_the_survivability_panel():
    """It was missing entirely, and alone accounted for 64px of shift."""
    assert 'class="survivability"' in render_report_shell("NVDA")


def test_the_layout_responds_to_narrow_screens():
    from views import _REPORT_CSS

    assert "@media (max-width: 720px)" in _REPORT_CSS


def test_wide_content_scrolls_inside_its_own_container():
    """The health table is too wide for a phone; it must scroll itself rather
    than make the whole page scroll sideways."""
    from views import _REPORT_CSS

    assert ".table-scroll { overflow-x: auto; }" in _REPORT_CSS


def test_the_404_offers_a_way_to_try_again():
    """The reason you are here is almost always a mistyped ticker, and the
    fix is to type another one - not to go back and start over."""
    html = render_not_found("Unknown ticker: ZZZZ")
    assert "<form" in html
    assert "Try another ticker" in html
    assert "inp.focus();" in html


def test_the_404_still_says_what_went_wrong():
    html = render_not_found("Unknown ticker: ZZZZ")
    assert "Unknown ticker: ZZZZ" in html
    assert 'href="/"' in html


def test_the_theme_applies_on_every_page():
    """Choosing light on a report and clicking the wordmark must not land you
    back in dark."""
    for html in (render_landing(), render_not_found("x"),
                 render_report_shell("NVDA")):
        assert "moat.theme" in html


def test_the_theme_is_applied_before_the_body():
    """Applied after paint, a chosen theme flashes the other one first."""
    html = render_landing()
    assert html.index("moat.theme") < html.index("<body>")


def test_the_transitional_aliases_are_gone():
    """They existed so un-migrated pages kept working. Every page is migrated."""
    assert "--ink:" not in _TOKENS
    assert "--paper:" not in _TOKENS
    assert "--rule:" not in _TOKENS
