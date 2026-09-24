"""A bounded TTL cache, shared by the places that memoize upstream calls.

WHY THIS EXISTS

    Three call sites needed the same thing and each grew its own version: the
    share price, the latest-10-K metadata, and the filing text. They differ in
    what they cache and for how long, but the mechanism - store a value with a
    timestamp, serve it while fresh, drop it when stale - is identical, and
    three copies of it is three places for the same bug.

WHY IT IS BOUNDED

    Ticker is user-supplied. An unbounded dict keyed on it is a memory leak
    with a public trigger: enumerate tickers and the process grows until it is
    killed. The filing text makes that concrete at roughly 70KB an entry.
    Eviction is least-recently-used, so the companies people actually look at
    stay resident.

WHAT IT DELIBERATELY DOES NOT DO

    It does not decide what is worth caching. A failure is a value like any
    other here, and the caller chooses - prices refuses to cache a failure so
    that a blip does not become permanent, while the filing lookup caches a
    genuine "this company has no 10-K". That judgment belongs with the caller
    who knows what the value means.
"""

import threading
import time
from collections import OrderedDict
from typing import Any

_MISS = object()


class TTLCache:
    """Thread-safe, size-bounded, time-expiring key/value store."""

    def __init__(self, ttl_seconds: float, max_entries: int = 128):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        """Return the cached value, or `default` if absent or stale."""
        with self._lock:
            entry = self._data.get(key, _MISS)
            if entry is _MISS:
                return default
            stored_at, value = entry
            if time.monotonic() - stored_at >= self.ttl_seconds:
                del self._data[key]
                return default
            self._data.move_to_end(key)   # mark as recently used
            return value

    def has(self, key: str) -> bool:
        """True if a fresh entry exists. Distinguishes a cached None from a
        miss, which matters when None is a meaningful value."""
        return self.get(key, _MISS) is not _MISS

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)   # evict least recently used

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
