"""Tests for the parallel prefetch phase.

Three upstreams, none depending on the others. Run in series they cost the
sum of their latencies; together they cost the slowest. The tests assert both
halves of that: that the work genuinely overlaps, and that a failure in one
upstream does the right thing rather than taking the report down.
"""

import threading
import time

import pytest

from moat import pipeline, timing


def _slow(seconds, value):
    def fn(ticker):
        time.sleep(seconds)
        return value

    return fn


def test_fetches_run_concurrently():
    """The whole point. Three 0.2s fetches in series would be 0.6s."""
    started = time.perf_counter()
    pipeline.prefetch(
        "MSFT",
        need_financials=True,
        fetch_financials=_slow(0.2, ("789019", "Microsoft", {"net_income": {}})),
        fetch_filing=_slow(0.2, {"text": "t", "url": "u", "report_date": "d"}),
        fetch_price=_slow(0.2, {"price": 1.0}),
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 0.45, f"took {elapsed:.2f}s - the fetches ran in series"


def test_all_three_results_are_returned():
    result = pipeline.prefetch(
        "MSFT",
        need_financials=True,
        fetch_financials=lambda t: ("789019", "Microsoft", {"net_income": {}}),
        fetch_filing=lambda t: {"text": "t", "url": "u", "report_date": "d"},
        fetch_price=lambda t: {"price": 1.0},
    )
    assert result.name == "Microsoft"
    assert result.series == {"net_income": {}}
    assert result.filing["url"] == "u"
    assert result.price == {"price": 1.0}


def test_financials_are_skipped_when_already_ingested():
    called = []
    result = pipeline.prefetch(
        "MSFT",
        need_financials=False,
        fetch_financials=lambda t: called.append(t),
        fetch_filing=lambda t: {"text": "t"},
        fetch_price=lambda t: None,
    )
    assert called == [], "re-fetched financials for a company already in the database"
    assert result.series is None
    assert result.filing == {"text": "t"}


def test_a_failing_filing_does_not_sink_the_report():
    """The computed figures do not come from the filing."""
    result = pipeline.prefetch(
        "MSFT",
        need_financials=False,
        fetch_financials=lambda t: None,
        fetch_filing=lambda t: (_ for _ in ()).throw(RuntimeError("EDGAR down")),
        fetch_price=lambda t: {"price": 1.0},
    )
    assert result.filing is None
    assert result.price == {"price": 1.0}
    assert "filing" in result.errors


def test_a_failing_price_does_not_sink_the_report():
    result = pipeline.prefetch(
        "MSFT",
        need_financials=False,
        fetch_financials=lambda t: None,
        fetch_filing=lambda t: {"text": "t"},
        fetch_price=lambda t: (_ for _ in ()).throw(RuntimeError("yfinance down")),
    )
    assert result.price is None
    assert result.filing == {"text": "t"}


def test_a_failing_financials_fetch_is_re_raised():
    """Without financials there is no report, so the endpoint must see it.

    Re-raised on the calling thread so the existing 404 and 502 handlers apply
    unchanged.
    """
    with pytest.raises(ValueError, match="Unknown ticker"):
        pipeline.prefetch(
            "ZZZZ",
            need_financials=True,
            fetch_financials=lambda t: (_ for _ in ()).throw(ValueError("Unknown ticker: ZZZZ")),
            fetch_filing=lambda t: None,
            fetch_price=lambda t: None,
        )


def test_the_other_fetches_still_complete_before_the_raise():
    """A failure must not abandon work already paid for."""
    done = []
    with pytest.raises(ValueError):
        pipeline.prefetch(
            "ZZZZ",
            need_financials=True,
            fetch_financials=lambda t: (_ for _ in ()).throw(ValueError("nope")),
            fetch_filing=lambda t: done.append("filing"),
            fetch_price=lambda t: done.append("price"),
        )
    assert sorted(done) == ["filing", "price"]


def test_stages_inside_workers_are_recorded():
    """ContextVars do not cross a thread boundary by themselves.

    If bind_context were dropped, the breakdown would silently omit every
    parallelised stage and the fetches would look instantaneous.
    """

    def fetch_with_stage(ticker):
        with timing.stage("inner.work"):
            return {"text": "t"}

    with timing.track("t") as t:
        pipeline.prefetch(
            "MSFT",
            need_financials=False,
            fetch_financials=lambda x: None,
            fetch_filing=fetch_with_stage,
            fetch_price=lambda x: None,
        )
    assert "inner.work" in [name for name, _ in t.stages]


def test_workers_are_named_for_debugging():
    seen = []
    pipeline.prefetch(
        "MSFT",
        need_financials=False,
        fetch_financials=lambda t: None,
        fetch_filing=lambda t: seen.append(threading.current_thread().name),
        fetch_price=lambda t: None,
    )
    assert any("moat-prefetch" in n for n in seen)
