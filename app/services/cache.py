"""Async TTL cache used to memoise search answers."""

import asyncio
import time
from typing import Any

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds or get_settings().cache_ttl_seconds
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._data[key]
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def size(self) -> int:
        return len(self._data)


_cache: AsyncTTLCache | None = None


def get_cache() -> AsyncTTLCache:
    global _cache
    if _cache is None:
        _cache = AsyncTTLCache()
    return _cache
