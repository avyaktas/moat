"""Tests for the shared TTL cache."""

from moat import cache


def test_stores_and_returns():
    c = cache.TTLCache(ttl_seconds=60)
    c.set("k", "v")
    assert c.get("k") == "v"


def test_missing_key_returns_default():
    c = cache.TTLCache(ttl_seconds=60)
    assert c.get("nope") is None
    assert c.get("nope", "fallback") == "fallback"


def test_entry_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    c = cache.TTLCache(ttl_seconds=10)
    c.set("k", "v")

    now[0] += 9
    assert c.get("k") == "v"
    now[0] += 2
    assert c.get("k") is None


def test_has_distinguishes_cached_none_from_miss():
    """A cached None is a real answer - "this company has no 10-K" - and must
    not look like an empty cache."""
    c = cache.TTLCache(ttl_seconds=60)
    c.set("k", None)
    assert c.has("k") is True
    assert c.has("other") is False


def test_evicts_least_recently_used():
    c = cache.TTLCache(ttl_seconds=60, max_entries=2)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")            # 'a' is now the most recently used
    c.set("c", 3)         # evicts 'b'

    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3


def test_stays_within_max_entries():
    """Ticker is user-supplied, so an unbounded cache is a public memory leak."""
    c = cache.TTLCache(ttl_seconds=60, max_entries=10)
    for i in range(1000):
        c.set(f"k{i}", i)
    assert len(c) == 10


def test_clear_empties_the_cache():
    c = cache.TTLCache(ttl_seconds=60)
    c.set("k", "v")
    c.clear()
    assert len(c) == 0
    assert c.get("k") is None


def test_overwriting_refreshes_the_timestamp(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    c = cache.TTLCache(ttl_seconds=10)
    c.set("k", "v1")
    now[0] += 9
    c.set("k", "v2")
    now[0] += 5
    assert c.get("k") == "v2", "a rewrite should restart the TTL"
