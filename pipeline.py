"""Building a report: the work, separated from the endpoints that serve it.

WHY THE FETCHES RUN AT ONCE

    Three upstreams are consulted for a cold ticker and none of them depends
    on the others' answers: EDGAR's companyfacts for the numbers, EDGAR's
    filing archive for the 10-K prose, and yfinance for the market price. Run
    in series they cost the sum of their latencies; run together they cost the
    slowest.

    The CIK is resolved first, on purpose. All three paths need it, and
    get_cik downloads the SEC's full ticker file on its first call - fanning
    out before it is warm would have three workers download the same megabyte
    at once. Resolving it once makes the lookups inside the workers free.

WHAT EACH WORKER IS ALLOWED TO DO

    Network and arithmetic only. None of them opens a database session: the
    financials write happens on the calling thread once the fetch has
    returned, which is why ingest was split into fetch_financials and
    store_financials. Sharing a Session across threads is not safe, and the
    split means nothing has to try.
"""

import concurrent.futures
import logging
from dataclasses import dataclass, field

import timing

logger = logging.getLogger(__name__)

# One worker per independent upstream. There is no point going wider: the work
# is three specific fetches, not a queue.
PREFETCH_WORKERS = 3


@dataclass
class Prefetched:
    """What the parallel fetch phase came back with.

    Every field is optional because every upstream is allowed to fail without
    taking the report down - except the financials fetch, whose exception is
    re-raised on the calling thread so the endpoint can map it to a 404 or a
    502 exactly as it did when the call was serial.
    """

    name: str | None = None
    series: dict | None = None
    filing: dict | None = None
    price: dict | None = None
    errors: dict[str, BaseException] = field(default_factory=dict)


def prefetch(ticker: str, *, need_financials: bool, fetch_financials,
             fetch_filing, fetch_price) -> Prefetched:
    """Run the independent upstream fetches concurrently.

    The three callables are injected rather than imported so this stays
    testable without a network, and so the module does not import half the
    application to describe an ordering.
    """
    result = Prefetched()

    jobs: dict[str, callable] = {
        "filing": lambda: fetch_filing(ticker),
        "price": lambda: fetch_price(ticker),
    }
    if need_financials:
        jobs["financials"] = lambda: fetch_financials(ticker)

    with timing.stage("prefetch"):
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=PREFETCH_WORKERS, thread_name_prefix="moat-prefetch"
        ) as pool:
            # bind_context so stage() inside a worker still records against
            # this request's breakdown. Copying the context inside the worker
            # would capture the worker's own empty one and record nothing.
            futures = {
                pool.submit(timing.bind_context(fn)): name
                for name, fn in jobs.items()
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    value = future.result()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    result.errors[name] = exc
                    logger.warning("prefetch %s failed for %s: %s: %s",
                                   name, ticker, type(exc).__name__, exc)
                    continue
                if name == "financials":
                    _cik, result.name, result.series = value
                elif name == "filing":
                    result.filing = value
                else:
                    result.price = value

    # The filing and the price are allowed to be missing - the report degrades
    # to computed figures, or to no valuation. Financials are not: without them
    # there is no report at all, so that failure is re-raised here, on the
    # calling thread, where the endpoint's existing handlers can see it.
    if "financials" in result.errors:
        raise result.errors["financials"]

    return result


# ----------------------------------------------------------------- events
#
# Building a report is a sequence of stages, and two callers want different
# things from it. The JSON endpoint wants the finished payload and nothing
# else. The streaming endpoint wants to say what is happening as it happens,
# and to show the computed figures before the model has written a word.
#
# Rather than implement it twice - which would guarantee the two drift - the
# build is a generator that yields these, and each caller keeps what it needs.


@dataclass
class Stage:
    """Progress on one stage of the build."""

    key: str
    label: str
    state: str                      # running | done | skipped | failed
    seconds: float | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        out = {"key": self.key, "label": self.label, "state": self.state}
        if self.seconds is not None:
            out["seconds"] = round(self.seconds, 2)
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class Partial:
    """Everything computed from filed data, before the model is consulted.

    Emitted so the page can show the scorecard, the figures and the health
    table at around two seconds rather than making the reader wait out the
    thirty the narrative takes. These numbers are final - the model does not
    revise them, it interprets them - so showing them early is honest.
    """

    payload: dict


@dataclass
class Result:
    """The finished report."""

    payload: dict


@dataclass
class Failure:
    """The build cannot continue, with something a person can read."""

    status: int
    title: str
    detail: str


# The stages a reader sees, in order. Labels live here so the page and the
# logs agree on what the application calls each step.
STAGE_LABELS = {
    "fetch": "Fetching SEC filings",
    "store": "Storing financials",
    "metrics": "Computing metrics",
    "synthesis": "Writing analysis",
}
