"""Tests for the price cache.

get_price used @lru_cache. lru_cache memoizes whatever the function returns,
including the None produced by the bare `except Exception` - so a single
yfinance blip disabled price and valuation for that ticker for the entire
process lifetime. The report silently lost its P/E and P/FCF and never got
them back until a redeploy.

Caching a success is the point. Caching a failure is a bug: it converts a
transient outage into a permanent one.
"""

import prices


def setup_function():
    prices.clear_price_cache()


def _ok(ticker):
    return {"price": 100.0, "market_cap": 1000.0, "shares_outstanding": 10.0}


def test_successful_price_is_returned():
    prices._fetch_price = _ok
    assert prices.get_price("MSFT")["price"] == 100.0


def test_successful_price_is_cached():
    calls = []

    def counting(ticker):
        calls.append(ticker)
        return _ok(ticker)

    prices._fetch_price = counting
    prices.get_price("MSFT")
    prices.get_price("MSFT")
    assert len(calls) == 1, "a successful lookup should be served from cache"


def test_failure_is_not_cached():
    """The regression: one blip must not disable the ticker forever."""
    calls = []

    def flaky(ticker):
        calls.append(ticker)
        if len(calls) == 1:
            return None          # transient failure
        return _ok(ticker)

    prices._fetch_price = flaky
    assert prices.get_price("MSFT") is None
    assert prices.get_price("MSFT")["price"] == 100.0, (
        "a retry after a transient failure must be allowed to succeed"
    )


def test_raised_exception_is_not_cached():
    calls = []

    def flaky(ticker):
        calls.append(ticker)
        if len(calls) == 1:
            raise RuntimeError("yfinance exploded")
        return _ok(ticker)

    prices._fetch_price = flaky
    assert prices.get_price("MSFT") is None
    assert prices.get_price("MSFT")["price"] == 100.0


def test_exception_does_not_propagate():
    """A price outage degrades the report; it must never take it down."""
    def boom(ticker):
        raise RuntimeError("network on fire")

    prices._fetch_price = boom
    assert prices.get_price("MSFT") is None


def test_cache_expires(monkeypatch):
    """A price frozen for the process lifetime is a stale valuation."""
    import cache

    calls = []

    def counting(ticker):
        calls.append(ticker)
        return _ok(ticker)

    now = [1000.0]
    # The clock now lives in the shared TTLCache rather than in prices.
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    prices._fetch_price = counting

    prices.get_price("MSFT")
    now[0] += prices.PRICE_TTL_SECONDS - 1
    prices.get_price("MSFT")
    assert len(calls) == 1, "still fresh, should not refetch"

    now[0] += 2
    prices.get_price("MSFT")
    assert len(calls) == 2, "past the TTL, should refetch"


def test_ticker_case_shares_one_cache_entry():
    calls = []

    def counting(ticker):
        calls.append(ticker)
        return _ok(ticker)

    prices._fetch_price = counting
    prices.get_price("msft")
    prices.get_price("MSFT")
    assert len(calls) == 1
