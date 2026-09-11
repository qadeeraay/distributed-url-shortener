import asyncio
import json
import logging
from typing import Optional, Dict, Any
import redis.asyncio as redis
from app.config import settings
from app.core.metrics import CACHE_OPERATIONS_TOTAL

logger = logging.getLogger("url_shortener.cache")

_redis_pool: Optional[redis.Redis] = None


async def get_redis_pool() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(settings.redis_url, decode_responses=True)
    else:
        try:
            current_loop = asyncio.get_running_loop()
            if getattr(_redis_pool, "_bound_loop", None) != id(current_loop):
                _redis_pool = redis.from_url(settings.redis_url, decode_responses=True)
                setattr(_redis_pool, "_bound_loop", id(current_loop))
        except RuntimeError:
            pass
    return _redis_pool


async def close_redis_pool() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None




async def get_redis() -> redis.Redis:
    return await get_redis_pool()


class URLCacheManager:
    """Manages Redis cache-aside reads, writes, and anti-penetration keys."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _url_key(self, short_code: str) -> str:
        return f"url:{short_code}"

    def _negative_key(self, short_code: str) -> str:
        return f"url:not_found:{short_code}"

    async def get_url(self, short_code: str) -> Optional[Dict[str, Any]]:
        val = await self.redis.get(self._url_key(short_code))
        if val is not None:
            CACHE_OPERATIONS_TOTAL.labels(status="hit").inc()
            return json.loads(val)
        return None

    async def set_url(
        self,
        short_code: str,
        data: Dict[str, Any],
        ttl: int = settings.URL_CACHE_TTL_SECONDS
    ) -> None:
        await self.redis.setex(self._url_key(short_code), ttl, json.dumps(data))

    async def invalidate_url(self, short_code: str) -> None:
        await self.redis.delete(self._url_key(short_code))
        await self.redis.delete(self._negative_key(short_code))

    async def is_negative_cached(self, short_code: str) -> bool:
        exists = await self.redis.exists(self._negative_key(short_code))
        if exists:
            CACHE_OPERATIONS_TOTAL.labels(status="negative_hit").inc()
            return True
        return False

    async def set_negative_cache(
        self,
        short_code: str,
        ttl: int = settings.NEGATIVE_CACHE_TTL_SECONDS
    ) -> None:
        await self.redis.setex(self._negative_key(short_code), ttl, "1")

