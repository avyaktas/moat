"""A small per-client token bucket for the endpoints that cost money.

WHAT THIS PROTECTS

    /brief and /report are the two endpoints that spend: each cache miss is an
    Anthropic call plus an SEC document fetch. Both are unauthenticated and
    publicly reachable, and /report?refresh= bypasses the cache outright. That
    combination lets anyone with the URL drain the API budget in a loop, and
    grow the briefs table without bound while doing it.

WHAT THIS IS NOT

    It is in-process and per-instance. It does not survive a restart and does
    not coordinate across replicas, so two instances behind a load balancer
    each allow the full rate. It keys on the client IP, which a determined
    caller can vary.

    So this is a speed bump against casual abuse and accidental loops - a
    misbehaving script, a crawler, someone holding down refresh - not a
    defence against a deliberate attacker. Doing that properly needs shared
    state (Redis) and probably authentication. Saying so here is better than
    implying a guarantee this does not provide.

WHY A TOKEN BUCKET

    A fixed window lets a caller spend the whole allowance in the last second
    of one window and again in the first second of the next, which is twice
    the intended rate at the worst moment. A bucket refills continuously, so
    the long-run rate holds while short bursts are still allowed - which is
    the shape real use has: someone loads a page, then a few more.
"""

import threading
import time

_lock = threading.Lock()
_buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, last_seen)


def clear() -> None:
    """Drop all buckets. Used by tests to isolate cases."""
    with _lock:
        _buckets.clear()


def allow(key: str, capacity: int, refill_per_minute: float) -> bool:
    """Spend one token for `key`. True if it was available.

    capacity is the burst size; refill_per_minute is the sustained rate. A
    capacity of 0 or less disables limiting entirely, which is how tests and
    local development opt out.

    Locked because FastAPI runs sync endpoints in a threadpool, so several
    requests genuinely touch this dict at once. The critical section is a
    couple of arithmetic operations, so contention is not a concern.
    """
    if capacity <= 0:
        return True

    now = time.monotonic()
    with _lock:
        tokens, last = _buckets.get(key, (float(capacity), now))
        # Refill for the time elapsed, never above the burst capacity.
        tokens = min(float(capacity), tokens + (now - last) * refill_per_minute / 60.0)
        if tokens < 1.0:
            _buckets[key] = (tokens, now)
            return False
        _buckets[key] = (tokens - 1.0, now)
        return True


def retry_after_seconds(refill_per_minute: float) -> int:
    """Whole seconds until one token is available, for the Retry-After header."""
    if refill_per_minute <= 0:
        return 60
    return max(1, int(60.0 / refill_per_minute))
