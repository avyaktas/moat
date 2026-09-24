"""Per-stage timing for a single request.

WHY A CONTEXTVAR RATHER THAN A PARAMETER

    The stages worth timing are spread across four modules - the CIK lookup and
    the EDGAR fetch live in ingest, the 10-K download in filings, synthesis in
    report, the cache write in main. Threading a timer argument through all of
    them would change signatures that are otherwise none of timing's business,
    and would make every caller responsible for passing it on.

    A context variable is set once at the top of a request and read wherever a
    stage happens to run. Where nothing set one - the ingest CLI, the tests,
    the offline replay - `stage()` still logs but records nothing, so
    instrumentation never depends on being inside a request.

THREADS

    ContextVars do not cross a ThreadPoolExecutor boundary by themselves. Any
    stage that runs in a worker thread has to carry the context over with
    contextvars.copy_context(), which is what run_in_context() below is for.
    Without it a parallelised stage would silently record nothing - the timing
    would not be wrong, it would simply be missing, which is worse.
"""

import contextvars
import logging
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_current: contextvars.ContextVar["Timings | None"] = contextvars.ContextVar(
    "moat_timings", default=None
)


class Timings:
    """Stage durations for one request, in the order they completed."""

    def __init__(self, label: str = ""):
        self.label = label
        self.stages: list[tuple[str, float]] = []
        self.started = time.perf_counter()

    def record(self, name: str, seconds: float) -> None:
        self.stages.append((name, seconds))

    @property
    def total(self) -> float:
        return time.perf_counter() - self.started

    def seconds_for(self, name: str) -> float:
        """Total time in one stage, summed if it ran more than once."""
        return sum(s for n, s in self.stages if n == name)

    def breakdown(self) -> str:
        """One line naming every stage and its share of the total."""
        total = self.total
        parts = [f"{n}={s:.2f}s" for n, s in self.stages]
        accounted = sum(s for _, s in self.stages)
        parts.append(f"other={max(0.0, total - accounted):.2f}s")
        return f"total={total:.2f}s " + " ".join(parts)


@contextmanager
def track(label: str):
    """Open a per-request breakdown and log it on the way out."""
    timings = Timings(label)
    token = _current.set(timings)
    try:
        yield timings
    finally:
        _current.reset(token)
        logger.info("timing %s | %s", label, timings.breakdown())


@contextmanager
def stage(name: str):
    """Time one stage, logging it and recording it against the request.

    Records on the way out even when the body raises, because a stage that
    failed slowly is exactly the one worth seeing in the breakdown.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        timings = _current.get()
        if timings is not None:
            timings.record(name, elapsed)
        logger.info("stage %s seconds=%.2f", name, elapsed)


def current() -> "Timings | None":
    """The breakdown for the request in progress, if there is one."""
    return _current.get()


def bind_context(fn, *args, **kwargs):
    """Bind fn to a copy of the CALLER's context, returning a zero-arg callable.

    Used when handing work to a thread pool so stage() inside the worker still
    finds the request's Timings. Without it, parallelised stages record nothing
    and the breakdown silently under-reports - which looks like a fast stage
    rather than a missing one.

    The copy has to be taken here, in the calling thread. Copying inside the
    worker would capture the worker's own empty context and achieve nothing,
    which is a mistake that produces no error and no data.
    """
    ctx = contextvars.copy_context()

    def runner():
        return ctx.run(fn, *args, **kwargs)

    return runner
