"""
Shared dependencies: API-key auth and a small in-process rate limiter.

The rate limiter exists for a specific reason: a single misbehaving client can
otherwise trigger hundreds of SIMBAD lookups and get the *server's* IP blocked
by CDS, breaking the app for everyone. It is deliberately simple (a sliding
window per client IP in memory) because a real deployment should put nginx or a
WAF in front anyway.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict

from fastapi import Header, HTTPException, Request, status

from .config import get_settings


# ---------------------------------------------------------------------- #
# API key
# ---------------------------------------------------------------------- #
async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """No-op unless ``EH_API_KEY`` is configured.

    Kept as a dependency so you can protect the prediction endpoint in
    production with a single env var and no code change.
    """
    expected = get_settings().api_key
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


# ---------------------------------------------------------------------- #
# Rate limiting
# ---------------------------------------------------------------------- #
class SlidingWindowLimiter:
    """Per-key sliding-window counter. Thread-safe, O(window) memory."""

    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> tuple[bool, int, float]:
        """Return ``(allowed, remaining, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                retry_after = max(0.0, bucket[0] + self.window_seconds - now)
                return False, 0, retry_after
            bucket.append(now)
            remaining = self.max_requests - len(bucket)
            return True, remaining, 0.0

    def prune(self) -> None:
        """Drop empty buckets so a long-running process does not leak memory."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            dead = [k for k, dq in self._hits.items() if not dq or dq[-1] < cutoff]
            for k in dead:
                self._hits.pop(k, None)


_LIMITER = SlidingWindowLimiter(max_requests=get_settings().rate_limit_per_minute, window_seconds=60)


def client_ip(request: Request) -> str:
    """Best-effort client IP, honouring a reverse proxy's X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request) -> None:
    settings = get_settings()
    if settings.rate_limit_per_minute <= 0:
        return
    key = client_ip(request)
    allowed, remaining, retry_after = _LIMITER.allow(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Slow down -- this protects the upstream SIMBAD service.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    # Opportunistic cleanup.
    if len(_LIMITER._hits) > 5000:  # noqa: SLF001 - internal housekeeping
        _LIMITER.prune()
