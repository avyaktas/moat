"""Tests for report data assembly.

Only the deterministic half is tested here - build_report_data takes rows
and a price dict and returns computed figures. The synthesis step is an
LLM call, tested separately via mocking at the endpoint level.

A tiny stand-in class replaces the ORM row so these tests need no database.
"""

from dataclasses import dataclass
from datetime import date

from moat.report import _growth, _row_to_dict, build_report_data


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

# --- what the model actually receives ---
#
# report.py's own _f() docstring says the model should see real numbers, not
# strings of 28-digit precision - but _f() was only ever applied to the `ttm`
# block. The scorecard, valuation and financial-health figures went through
# json.dumps(default=str), which turned every Decimal off a Numeric column
# into a quoted string like "20.83333333333333333333333333".
#
# "The model narrates but never calculates" is weaker when the figures arrive
# as strings: a model handed "2500" has to decide it is a number before it can
# reason about it, and the prompt's "if a figure is null, say so" contract
# blurs when nulls and values are both text.

from decimal import Decimal

import moat.report as report_module


class _CapturingClient:
    """Stands in for Anthropic, recording the prompt instead of sending it."""

    def __init__(self, reply: str):
        self.captured = None
        self._reply = reply
        self.messages = self

    def create(self, **kwargs):
        self.captured = kwargs["messages"][0]["content"]

        class _Block:
            type = "text"
            text = self._reply_text

        block = _Block()
        block.text = self._reply

        class _Response:
            content = [block]

        return _Response()

    _reply_text = ""


def _decimal_rows(n: int = 8) -> list[FakeRow]:
    """Rows carrying Decimals, exactly as they come off Numeric columns."""
    return [
        FakeRow(
            period_end=date(2026, 3, 31),
            revenue=Decimal("100"),
            net_income=Decimal("30"),
            free_cash_flow=Decimal("25"),
            total_debt=Decimal("40"),
            shareholders_equity=Decimal("400"),
            cash=Decimal("32"),
            short_term_investments=Decimal("46"),
        )
        for _ in range(n)
    ]


def _synthesis_payload() -> str:
    """Run synthesize against a capturing client and return the prompt text."""
    data = build_report_data(_decimal_rows(), {"market_cap": Decimal("2500")})
    client = _CapturingClient('{"verdict": "WATCH-CASE", "risks": []}')
    report_module.synthesize(data, "ITEM 1A. RISK FACTORS text", "Test Co",
                             client=client)
    return client.captured


def test_synthesis_payload_has_no_stringified_numbers():
    """No computed figure reaches the model wrapped in quotes."""
    import json as _json
    import re

    payload = _synthesis_payload()
    figures = re.search(r"<computed_figures>\n(.*)\n</computed_figures>",
                        payload, re.DOTALL).group(1)
    parsed = _json.loads(figures)

    def walk(node, path="figures"):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            # A numeric-looking string is a Decimal that escaped coercion.
            assert not re.fullmatch(r"-?\d+(\.\d+)?", node), (
                f"{path} reached the model as the string {node!r}, not a number"
            )

    walk(parsed)


def test_synthesis_payload_keeps_full_precision_as_floats():
    """Coercion must not stringify, and must not lose the value either."""
    import json as _json
    import re

    payload = _synthesis_payload()
    figures = re.search(r"<computed_figures>\n(.*)\n</computed_figures>",
                        payload, re.DOTALL).group(1)
    parsed = _json.loads(figures)

    assert isinstance(parsed["scorecard"]["valuation"]["market_cap"], (int, float))
    assert parsed["scorecard"]["valuation"]["market_cap"] == 2500.0
    # p_e = 2500 / 120 -> a repeating Decimal; it must arrive as a float.
    p_e = parsed["scorecard"]["valuation"]["p_e"]
    assert isinstance(p_e, float)
    assert abs(p_e - 2500 / 120) < 1e-9


def test_synthesis_payload_preserves_nulls_as_json_null():
    """An unavailable figure must stay null, never the string 'None'."""
    import json as _json
    import re

    data = build_report_data(
        [FakeRow(period_end=date(2026, 3, 31)) for _ in range(8)], None
    )
    client = _CapturingClient('{"verdict": "WATCH-CASE", "risks": []}')
    report_module.synthesize(data, "filing text", "Test Co", client=client)
    figures = re.search(r"<computed_figures>\n(.*)\n</computed_figures>",
                        client.captured, re.DOTALL).group(1)
    parsed = _json.loads(figures)

    assert parsed["ttm"]["revenue"] is None
    assert "None" not in figures


# --- synthesis failure must be unmistakable ---
#
# synthesize used to RETURN {"error": ...} when the model's reply would not
# parse. main.py guarded the cache write with `if narrative is not None`, and
# that dict is not None - so a broken narrative was persisted and served as
# NO VERDICT for the full 7-day TTL. Failure has to be a raise, so there is
# exactly one way to fail and a caller cannot mistake it for a result.

def test_synthesize_raises_on_unparseable_reply():
    from moat.report import SynthesisError

    client = _CapturingClient("I'm afraid I can't help with that.")
    try:
        report_module.synthesize({"ttm": {}}, "filing text", "Test Co",
                                 client=client)
    except SynthesisError as e:
        assert "raw" in str(e) or client.captured is not None
    else:
        raise AssertionError("synthesize returned instead of raising")


def test_synthesize_error_is_not_a_dict_with_error_key():
    """The specific regression: a truthy failure value reaching the caller."""
    from moat.report import SynthesisError

    client = _CapturingClient("not json at all")
    result = None
    try:
        result = report_module.synthesize({"ttm": {}}, "filing", "Co",
                                          client=client)
    except SynthesisError:
        pass
    assert result is None, (
        "synthesize returned a value on failure; main.py's `narrative is not "
        "None` guard would cache it"
    )


def test_synthesize_keeps_the_raw_reply_for_debugging():
    from moat.report import SynthesisError

    client = _CapturingClient("```not json```")
    try:
        report_module.synthesize({"ttm": {}}, "filing", "Co", client=client)
    except SynthesisError as e:
        assert e.raw is not None


# --- the declared response contract must match what is actually produced ---
#
# response_model filters as well as documents: a field absent from the model
# is dropped from the response. So a model that drifts from what
# build_report_data returns is silent data loss, not just stale docs.

def test_real_report_data_satisfies_the_declared_response_model():
    from moat.schemas import ReportDataOut

    data = build_report_data(_decimal_rows(8), {
        "price": Decimal("512.30"), "market_cap": Decimal("3700000000000"),
        "shares_outstanding": Decimal("7430000000"),
    })
    validated = ReportDataOut.model_validate(data)

    # Nothing silently dropped on the way through.
    assert validated.ttm.revenue == 400.0
    assert validated.scorecard.summary.evaluable >= 1
    assert validated.scorecard.valuation.market_cap == 3.7e12
    assert validated.price is not None
    assert "survivability" in validated.scorecard.financial_health


def test_report_data_without_price_still_validates():
    from moat.schemas import ReportDataOut

    validated = ReportDataOut.model_validate(build_report_data(_decimal_rows(8), None))
    assert validated.price is None
    assert validated.scorecard.valuation.p_e is None


def test_report_data_with_no_metrics_still_validates():
    """Every figure null is the honest-nulls case, and must serialize."""
    from moat.schemas import ReportDataOut

    rows = [FakeRow(period_end=date(2026, 3, 31)) for _ in range(8)]
    validated = ReportDataOut.model_validate(build_report_data(rows, None))
    assert validated.ttm.revenue is None
    assert validated.scorecard.summary.unknown == 6


def test_every_scorecard_check_satisfies_the_check_model():
    from moat.schemas import CheckOut

    data = build_report_data(_decimal_rows(8), None)
    for check in data["scorecard"]["checks"]:
        validated = CheckOut.model_validate(check)
        assert validated.status in {"PASS", "FAIL", "UNKNOWN"}


def test_synthesize_parses_a_raw_control_character():
    """The failure seen live: a literal newline inside a quoted passage.

    A model copying a filing writes the line break rather than escaping it,
    and json.loads rejects that by default - discarding an entire usable
    synthesis over a character with no semantic content.
    """
    reply = (
        '{"verdict": "WATCH-CASE", "reasoning": "Line one' + chr(10) + 'line two",'
        ' "risks": []}'
    )
    client = _CapturingClient(reply)
    result = report_module.synthesize({"ttm": {}}, "filing text", "Co", client=client)
    assert result["verdict"] == "WATCH-CASE"
    assert chr(10) in result["reasoning"]


def test_synthesize_still_raises_on_genuinely_broken_json():
    from moat.report import SynthesisError

    client = _CapturingClient("not json in any sense")
    try:
        report_module.synthesize({"ttm": {}}, "filing", "Co", client=client)
    except SynthesisError:
        pass
    else:
        raise AssertionError("tolerating control characters swallowed a real failure")


# --- truncation is a distinct failure, and retrying it is pointless ---
#
# Measured on NVDA: the synthesis prompt routinely lands near the ceiling
# (2,978 output tokens against a 4,000 cap). When five or six verbatim quotes
# push it over, the response is cut mid-string, json.loads reports
# "Unterminated string", and the retry produces the same truncation - two full
# 32-second calls for no verdict at all.

class _StopReasonClient:
    """Stands in for Anthropic, reporting a chosen stop_reason."""

    def __init__(self, reply: str, stop_reason: str = "end_turn"):
        self._reply = reply
        self._stop_reason = stop_reason
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        reply, stop_reason = self._reply, self._stop_reason

        class _B:
            type = "text"
            text = reply

        class _R:
            content = [_B()]

        _R.stop_reason = stop_reason
        return _R()


def test_truncated_response_raises_truncation_error():
    from moat.report import SynthesisTruncated

    client = _StopReasonClient('{"verdict": "WATCH-CASE", "risks": [{"quote": "unte',
                               stop_reason="max_tokens")
    try:
        report_module.synthesize({"ttm": {}}, "filing", "Co", client=client)
    except SynthesisTruncated as exc:
        assert "max_tokens" in str(exc) or "truncat" in str(exc).lower()
    else:
        raise AssertionError("a truncated response should raise SynthesisTruncated")


def test_truncation_error_is_a_synthesis_error():
    """moat.main.py's handler catches SynthesisError; truncation must not escape it."""
    from moat.report import SynthesisError, SynthesisTruncated

    assert issubclass(SynthesisTruncated, SynthesisError)


def test_complete_response_is_unaffected_by_the_stop_reason_check():
    client = _StopReasonClient('{"verdict": "BUY-CASE", "risks": []}',
                               stop_reason="end_turn")
    result = report_module.synthesize({"ttm": {}}, "filing", "Co", client=client)
    assert result["verdict"] == "BUY-CASE"


def test_missing_stop_reason_does_not_break_synthesis():
    """A stub client without stop_reason must still work - several tests use one."""
    client = _CapturingClient('{"verdict": "WATCH-CASE", "risks": []}')
    result = report_module.synthesize({"ttm": {}}, "filing", "Co", client=client)
    assert result["verdict"] == "WATCH-CASE"


def test_max_tokens_leaves_headroom_over_observed_output():
    """2,978 output tokens were observed against a 4,000 cap on a real filing.

    That headroom is too thin: a slightly longer set of quotes truncates the
    JSON and wastes the whole call. This pins the ceiling well clear of it.
    """
    from moat.report import SYNTHESIS_MAX_TOKENS

    assert SYNTHESIS_MAX_TOKENS >= 8000
