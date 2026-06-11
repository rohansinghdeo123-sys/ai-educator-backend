"""In-process rate limiting and per-user daily quota counters.

NOTE: These stores are per-worker and reset on restart. They are preserved here
unchanged from the original implementation; moving them to Redis is tracked as a
separate production-readiness item.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import date
from hashlib import sha256
from threading import Lock
from typing import Dict

from fastapi import Request

from app import config


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: Dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0


class DailyQuotaStore:
    def __init__(self) -> None:
        self._counts: Dict[str, int] = defaultdict(int)
        self._lock = Lock()

    def consume(self, user_id: str, quota_name: str, limit: int) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        day = date.today().isoformat()
        safe_user = sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        key = f"{day}:{quota_name}:{safe_user}"
        with self._lock:
            current = self._counts[key]
            if current >= limit:
                return False, current
            self._counts[key] = current + 1
            return True, self._counts[key]


rate_limiter = SlidingWindowLimiter()
daily_quotas = DailyQuotaStore()


def client_rate_key(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    client_host = forwarded or (request.client.host if request.client else "unknown")
    auth_header = request.headers.get("authorization") or ""
    if auth_header:
        token_hash = sha256(auth_header.encode("utf-8")).hexdigest()[:16]
        return f"auth:{token_hash}:{request.url.path}"
    return f"ip:{client_host}:{request.url.path}"


def minute_limit_for_path(path: str) -> int:
    if path.startswith("/admin"):
        return config.ADMIN_RATE_LIMIT_PER_MINUTE
    if path in config.AI_RATE_LIMIT_PATHS or path.startswith("/coach/autonomous-study"):
        return config.AI_RATE_LIMIT_PER_MINUTE
    return config.RATE_LIMIT_PER_MINUTE
