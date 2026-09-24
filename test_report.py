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

import report as report_module


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
