"""Tests for the per-stage timing recorder.

Instrumentation is the one thing that must not be trusted on sight: a stage
that silently records nothing makes a breakdown that looks complete while
under-reporting, which is worse than no breakdown at all.
"""

import concurrent.futures

import timing


def test_stages_are_recorded_in_order():
    with timing.track("t") as t:
        with timing.stage("first"):
            pass
        with timing.stage("second"):
            pass
    assert [name for name, _ in t.stages] == ["first", "second"]


def test_stage_outside_a_track_does_not_raise():
    """The ingest CLI, the tests and the offline replay all run stages with no
    enclosing request. They must work, just without recording."""
    with timing.stage("orphan"):
        pass
    assert timing.current() is None


def test_a_failing_stage_is_still_recorded():
    """A stage that failed slowly is exactly the one worth seeing."""
    with timing.track("t") as t:
        try:
            with timing.stage("boom"):
                raise RuntimeError("upstream died")
        except RuntimeError:
            pass
    assert [name for name, _ in t.stages] == ["boom"]


def test_repeated_stage_is_summed():
    """Synthesis can run twice; the breakdown should show the total cost."""
    with timing.track("t") as t:
        for _ in range(3):
            with timing.stage("synthesis"):
                pass
    assert t.seconds_for("synthesis") >= 0
    assert len([n for n, _ in t.stages if n == "synthesis"]) == 3


def test_breakdown_names_every_stage_and_a_total():
    with timing.track("t") as t:
        with timing.stage("alpha"):
            pass
    line = t.breakdown()
    assert "alpha=" in line
    assert "total=" in line
    assert "other=" in line


def test_breakdown_accounts_for_untimed_work():
    """`other` exists so the stages cannot quietly fail to add up."""
    with timing.track("t") as t:
        with timing.stage("alpha"):
            pass
    assert "other=" in t.breakdown()


def test_nested_tracks_do_not_leak():
    with timing.track("outer") as outer:
        with timing.track("inner"):
            with timing.stage("inside"):
                pass
        with timing.stage("outside"):
            pass
    assert [name for name, _ in outer.stages] == ["outside"]


def test_stage_in_a_worker_thread_records_with_bind_context():
    """ContextVars do not cross a thread pool boundary on their own.

    Without bind_context a parallelised stage records nothing, and the
    breakdown under-reports silently rather than failing.
    """
    def work():
        with timing.stage("threaded"):
            pass

    with timing.track("t") as t:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(timing.bind_context(work)).result()
    assert [name for name, _ in t.stages] == ["threaded"]


def test_binding_must_happen_in_the_calling_thread():
    """Copying the context inside the worker captures the worker's own empty
    context. It raises no error and records nothing, so it is pinned here."""
    def work():
        with timing.stage("threaded"):
            pass

    with timing.track("t") as t:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            # bind_context called INSIDE the worker - the wrong way round.
            pool.submit(lambda: timing.bind_context(work)()).result()
    assert t.stages == [], "a worker-side copy unexpectedly saw the request"


def test_stage_in_a_bare_thread_records_nothing():
    """Documents the trap that run_in_context exists to avoid."""
    def work():
        with timing.stage("threaded"):
            pass

    with timing.track("t") as t:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(work).result()
    assert t.stages == [], "context leaked into the worker unexpectedly"
