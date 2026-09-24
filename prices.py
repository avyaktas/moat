"""Current market price and share count.

Prices are not in SEC filings - they come from a market data source.
This is the one place the pipeline departs from primary-source data,
and it is deliberate: a filing cannot tell you what the market is
charging today. Fundamentals remain EDGAR-only.

yfinance is unofficial and occasionally breaks; failures return None
rather than raising, so a price outage degrades the report to a
quality-only assessment instead of taking the endpoint down.

WHY THIS CACHE IS HAND-ROLLED RATHER THAN @lru_cache

    lru_cache memoizes whatever the function returns, and this function
    returns None on failure. So a single yfinance blip cached None and
    disabled price and valuation for that ticker for the entire process
    lifetime - a transient outage converted into a permanent one, silently,
    with the report simply missing its P/E until the next redeploy.

    lru_cache also never expires, which is the opposite of what a price
    needs. A share price frozen for the lifetime of a long-running process
    is not a cache hit, it is a wrong number in a valuation.

    So: successes are cached with a TTL, failures are not cached at all.
"""

import logging

from cache import TTLCache

logger = logging.getLogger(__name__)

# Long enough to absorb a burst of requests for the same ticker, short enough
# that a valuation multiple is never built on a badly stale price.
PRICE_TTL_SECONDS = 900  # 15 minutes

_cache = TTLCache(ttl_seconds=PRICE_TTL_SECONDS, max_entries=512)


def clear_price_cache() -> None:
    """Drop every cached price. Used by tests to isolate cases."""
    _cache.clear()


def _fetch_price(ticker: str) -> dict | None:
    """Fetch live price data. Separated from caching so the transport can be
    swapped in tests without reaching the network."""
    import yfinance as yf

    info = yf.Ticker(ticker).info
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None:
        return None
    return {
        "price": price,
        "market_cap": info.get("marketCap"),
        "shares_outstanding": info.get("sharesOutstanding"),
    }


def get_price(ticker: str) -> dict | None:
    """Return current price, market cap, and shares outstanding, or None.

    A cached success is served for PRICE_TTL_SECONDS. A failure is never
    cached, so the next request retries rather than inheriting the outage.
    """
    ticker = ticker.upper()

    cached = _cache.get(ticker)
    if cached is not None:
        return cached

    try:
        data = _fetch_price(ticker)
    except Exception as exc:
        # yfinance is unofficial and fails in many shapes - HTTP errors, JSON
        # decode errors, schema changes. Log the cause rather than swallowing
        # it entirely, then degrade.
        logger.warning("price lookup failed for %s: %s: %s",
                       ticker, type(exc).__name__, exc)
        return None

    if data is None:
        logger.info("no price available for %s", ticker)
        return None

    _cache.set(ticker, data)
    return data
