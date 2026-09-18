"""
Cache abstraction.

Two interchangeable backends:

* :class:`InMemoryCache`  -- zero-dependency, per-process. Perfect for local
  development and single-worker deployments.
* :class:`RedisCache`     -- shared across workers / instances. This is what you
  want in production so that N uvicorn workers do not each fire their own
  SIMBAD queries (which is how IP bans happen).

Both implement the same tiny surface: ``get_json`` / ``set_json`` / ``delete``.
Values are always JSON-serialisable dicts so the two backends stay
byte-compatible.

Negative caching
----------------
``set_json(..., ttl=negative_ttl)`` is used by the SIMBAD resolver to store
``{"found": false, "reason": ...}`` payloads. Without this, every request that
includes an unresolvable star re-hits SIMBAD -- the single most common cause of
getting throttled or banned by CDS.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("eclipse_hunter.cache")


class BaseCache:
    """Minimal interface implemented by every backend."""

    name: str = "base"

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def set_json(self, key: str, value: Dict[str, Any], ttl: int) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def stats(self) -> Dict[str, Any]:
        return {"backend": self.name}

    # -- helpers ------------------------------------------------------- #
    @staticmethod
    def jittered(ttl: int, jitter: float = 0.1) -> int:
        """Add +/- jitter so a batch of keys does not expire simultaneously."""
        if ttl <= 0 or jitter <= 0:
            return max(ttl, 0)
        span = int(ttl * jitter)
        return ttl + random.randint(-span, span) if span else ttl


class InMemoryCache(BaseCache):
    """Thread-safe LRU + TTL dict.

    Deliberately simple: an ``OrderedDict`` gives us O(1) LRU eviction and we
    lazily expire entries on read plus a cheap sweep on write.
    """

    name = "memory"

    def __init__(self, max_items: int = 20000) -> None:
        self._store: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.RLock()
        self._max_items = max(64, int(max_items))
        self._hits = 0
        self._misses = 0
        self._expired = 0

    # ------------------------------------------------------------------ #
    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                self._misses += 1
                return None
            expires_at, value = item
            if expires_at <= now:
                # lazily evict
                self._store.pop(key, None)
                self._expired += 1
                self._misses += 1
                return None
            # mark as recently used
            self._store.move_to_end(key)
            self._hits += 1
            # Deep-ish copy so callers cannot mutate the cached payload.
            return json.loads(json.dumps(value))

    def set_json(self, key: str, value: Dict[str, Any], ttl: int) -> None:
        if ttl <= 0:
            return
        expires_at = time.time() + ttl
        with self._lock:
            self._store[key] = (expires_at, json.loads(json.dumps(value)))
            self._store.move_to_end(key)
            while len(self._store) > self._max_items:
                self._store.popitem(last=False)
            # Occasional sweep of already-expired keys (cheap, amortised).
            if len(self._store) % 512 == 0:
                self._sweep(now=expires_at - ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def _sweep(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        dead = [k for k, (exp, _) in self._store.items() if exp <= now]
        for k in dead:
            self._store.pop(k, None)
        self._expired += len(dead)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "backend": self.name,
                "entries": len(self._store),
                "max_entries": self._max_items,
                "hits": self._hits,
                "misses": self._misses,
                "expired": self._expired,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
            }


class RedisCache(BaseCache):
    """Redis-backed cache using JSON strings and native key expiry."""

    name = "redis"

    def __init__(self, url: str, socket_timeout: float = 3.0) -> None:
        import redis  # imported lazily so the dependency stays optional

        self._redis = redis.Redis.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
            health_check_interval=30,
        )
        # Fail fast at startup rather than on the first user request.
        self._redis.ping()
        self._hits = 0
        self._misses = 0

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self._redis.get(key)
        except Exception as exc:  # pragma: no cover - network dependent
            log.warning("redis GET failed for %s: %s", key, exc)
            return None
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("redis value for %s is not valid JSON; dropping", key)
            self.delete(key)
            return None

    def set_json(self, key: str, value: Dict[str, Any], ttl: int) -> None:
        if ttl <= 0:
            return
        try:
            self._redis.set(key, json.dumps(value, separators=(",", ":")), ex=ttl)
        except Exception as exc:  # pragma: no cover
            log.warning("redis SET failed for %s: %s", key, exc)

    def delete(self, key: str) -> None:
        try:
            self._redis.delete(key)
        except Exception as exc:  # pragma: no cover
            log.warning("redis DEL failed for %s: %s", key, exc)

    def clear(self) -> None:
        try:
            self._redis.flushdb()
        except Exception as exc:  # pragma: no cover
            log.warning("redis FLUSHDB failed: %s", exc)

    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        info: Dict[str, Any] = {
            "backend": self.name,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total else 0.0,
        }
        try:
            dbinfo = self._redis.info("keyspace")
            info["keyspace"] = dbinfo
        except Exception:
            pass
        return info


class NullCache(BaseCache):
    """Disables caching entirely. Only useful in tests."""

    name = "null"

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        return None

    def set_json(self, key: str, value: Dict[str, Any], ttl: int) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def clear(self) -> None:
        return None


# ---------------------------------------------------------------------- #
# Factory + process-wide singleton
# ---------------------------------------------------------------------- #
_CACHE: Optional[BaseCache] = None
_CACHE_LOCK = threading.Lock()


def build_cache(backend: str, redis_url: str = "", max_items: int = 20000) -> BaseCache:
    backend = (backend or "memory").lower()
    if backend == "null":
        return NullCache()
    if backend == "redis":
        try:
            return RedisCache(redis_url)
        except Exception as exc:
            log.error("Redis unavailable (%s) -- falling back to in-memory cache", exc)
            return InMemoryCache(max_items)
    return InMemoryCache(max_items)


def get_cache() -> BaseCache:
    """Return the process-wide cache, creating it on first use."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            from .config import get_settings

            s = get_settings()
            backend = s.resolved_cache_backend
            _CACHE = build_cache(backend, s.redis_url, s.memory_cache_max_items)
            log.info("Cache backend initialised: %s", _CACHE.name)
    return _CACHE


def set_cache(instance: BaseCache) -> None:
    """Override the singleton (used by tests and by FastAPI startup)."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = instance


# ---------------------------------------------------------------------- #
# Key builders -- keep every cache key in one place
# ---------------------------------------------------------------------- #
KEY_PREFIX = "eh:v1"


def ephem_key(filename: str, etag: str = "") -> str:
    return f"{KEY_PREFIX}:ephem:{filename}:{etag or 'latest'}"


def simbad_key(identifier: str) -> str:
    return f"{KEY_PREFIX}:simbad:{identifier.strip().lower()}"


def prediction_key(params: Dict[str, Any]) -> str:
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    import hashlib

    digest = hashlib.sha256(blob.encode()).hexdigest()[:32]
    return f"{KEY_PREFIX}:night:{digest}"
