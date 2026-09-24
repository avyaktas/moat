"""Tests for the token bucket."""

import ratelimit


def setup_function():
    ratelimit.clear()


def test_allows_up_to_capacity():
    assert all(ratelimit.allow("ip", capacity=3, refill_per_minute=60)
               for _ in range(3))


def test_blocks_past_capacity():
    for _ in range(3):
        ratelimit.allow("ip", 3, 60)
    assert ratelimit.allow("ip", 3, 60) is False


def test_keys_are_independent():
    for _ in range(3):
        ratelimit.allow("a", 3, 60)
    assert ratelimit.allow("a", 3, 60) is False
    assert ratelimit.allow("b", 3, 60) is True


def test_zero_capacity_disables_limiting():
    assert all(ratelimit.allow("ip", 0, 60) for _ in range(100))


def test_bucket_refills_over_time(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])

    for _ in range(3):
        ratelimit.allow("ip", 3, refill_per_minute=60)
    assert ratelimit.allow("ip", 3, 60) is False

    now[0] += 1.0          # 60/min = one token per second
    assert ratelimit.allow("ip", 3, 60) is True


def test_refill_never_exceeds_capacity(monkeypatch):
    """A long idle period must not bank unlimited tokens."""
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])

    ratelimit.allow("ip", 3, 60)
    now[0] += 3600        # an hour idle
    assert all(ratelimit.allow("ip", 3, 60) for _ in range(3))
    assert ratelimit.allow("ip", 3, 60) is False, "burst capped at capacity"


def test_sustained_rate_holds(monkeypatch):
    """Over a long window the long-run rate must not exceed the refill rate."""
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])

    allowed = 0
    for _ in range(600):          # 600 attempts, one every 0.1s = 60s total
        if ratelimit.allow("ip", capacity=5, refill_per_minute=60):
            allowed += 1
        now[0] += 0.1
    # 5 burst + ~60 refilled over the minute.
    assert allowed <= 66, f"allowed {allowed}, above the sustained rate"
    assert allowed >= 60


def test_retry_after_is_at_least_one_second():
    assert ratelimit.retry_after_seconds(60) == 1
    assert ratelimit.retry_after_seconds(6) == 10
    assert ratelimit.retry_after_seconds(0) == 60
