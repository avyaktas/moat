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

# How many stages are open on this thread. A worker inherits the caller's depth
# through the copied context, so a fetch running inside the prefetch is
# correctly recorded as nested rather than as a second top-level stage.
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("moat_depth", default=0)


class Timings:
    """Stage durations for one request, in the order they completed.

    Stages nest: the parallel prefetch contains the EDGAR and price fetches
    that run inside it. Depth is recorded alongside each duration so the
    breakdown can add up only the outermost ones - summing all of them would
    double-count the nested work and make the unaccounted remainder look like
    zero when it is really negative.
    """

    def __init__(self, label: str = ""):
        self.label = label
        self.stages: list[tuple[str, float]] = []
        self.depths: list[int] = []
        self.started = time.perf_counter()

    def record(self, name: str, seconds: float, depth: int = 0) -> None:
        self.stages.append((name, seconds))
        self.depths.append(depth)

    @property
    def total(self) -> float:
        return time.perf_counter() - self.started

    def seconds_for(self, name: str) -> float:
        """Total time in one stage, summed if it ran more than once."""
        return sum(s for n, s in self.stages if n == name)

    def breakdown(self) -> str:
        """One line naming every stage and its share of the total.

        Nested stages are shown with a dot prefix so a reader can see that
        they sit inside the stage above rather than beside it.
        """
        total = self.total
        parts = []
        for (name, seconds), depth in zip(self.stages, self.depths, strict=True):
            parts.append(f"{'.' * depth}{name}={seconds:.2f}s")
        accounted = sum(
            s for (_, s), d in zip(self.stages, self.depths, strict=True) if d == 0
        )
        parts.append(f"other={max(0.0, total - accounted):.2f}s")
        return f"total={total:.2f}s " + " ".join(parts)


@contextmanager
def track(label: str):
    """Open a per-request breakdown and log it on the way out."""
    timings = Timings(label)
    token = _current.set(timings)
    depth_token = _depth.set(0)
    try:
        yield timings
    finally:
        _depth.reset(depth_token)
        _current.reset(token)
        logger.info("timing %s | %s", label, timings.breakdown())


@contextmanager
def stage(name: str):
    """Time one stage, logging it and recording it against the request.

    Records on the way out even when the body raises, because a stage that
    failed slowly is exactly the one worth seeing in the breakdown.
    """
    start = time.perf_counter()
    depth = _depth.get()
    token = _depth.set(depth + 1)
    try:
        yield
    finally:
        _depth.reset(token)
        elapsed = time.perf_counter() - start
        timings = _current.get()
        if timings is not None:
            timings.record(name, elapsed, depth)
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
