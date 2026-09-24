"""The engine's pool configuration is load-bearing, so it is asserted.

Railway closes idle connections and this service is idle most of the time.
Without pool_pre_ping the first request after a quiet period fails on a
connection the pool still believes is open - intermittently, in production
only, and never in a test. That makes it exactly the kind of setting that
gets "cleaned up" by someone simplifying create_engine later.
"""

import database


def test_pre_ping_is_enabled():
    assert database.engine.pool._pre_ping is True, (
        "pool_pre_ping guards against stale connections after an idle period"
    )


def test_connections_are_recycled():
    assert database.engine.pool._recycle == 1800


def test_pool_sizing_is_explicit():
    assert database.engine.pool.size() == 5
    assert database.engine.pool._max_overflow == 10


def test_get_db_closes_the_session():
    closed = []

    class _Session:
        def close(self):
            closed.append(True)

    original = database.SessionLocal
    database.SessionLocal = lambda: _Session()
    try:
        gen = database.get_db()
        next(gen)
        list(gen)          # exhaust, triggering the finally
    except StopIteration:
        pass
    finally:
        database.SessionLocal = original

    assert closed == [True], "the session must be closed even on the happy path"
