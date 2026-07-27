"""Current market price and share count.

Prices are not in SEC filings - they come from a market data source.
This is the one place the pipeline departs from primary-source data,
and it is deliberate: a filing cannot tell you what the market is
charging today. Fundamentals remain EDGAR-only.

yfinance is unofficial and occasionally breaks; failures return None
rather than raising, so a price outage degrades the report to a
quality-only assessment instead of taking the endpoint down.
"""


from functools import lru_cache

@lru_cache(maxsize=256)
def get_price(ticker: str) -> dict | None:
    '''Return curr price, market cap, and shared outstanding. or None'''
    try: 
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
    except Exception:
        return None