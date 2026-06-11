"""Tiny thread-safe in-process TTL cache.

Used to absorb hot, repeated reads (leaderboard Firebase lookups, admin
console polling). Per-process by design: entries are short-lived enough that
multi-worker divergence is acceptable, and no new infrastructure is needed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Hashable, Tuple


class TTLCache:
    def __init__(self, max_entries: int = 256, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: Dict[Hashable, Tuple[float, Any]] = {}

    def get_or_build(self, key: Hashable, ttl_seconds: float, builder: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]

        # Build outside the lock so a slow builder never blocks other keys.
        # Concurrent misses on the same key may build twice; last write wins.
        value = builder()
        with self._lock:
            if len(self._entries) >= self._max_entries:
                self._evict_expired_locked(now)
            if len(self._entries) >= self._max_entries:
                oldest_key = min(self._entries, key=lambda item: self._entries[item][0])
                self._entries.pop(oldest_key, None)
            self._entries[key] = (now + ttl_seconds, value)
        return value

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict_expired_locked(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)
