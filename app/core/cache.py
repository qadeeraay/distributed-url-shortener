import json
import redis.asyncio as redis
from typing import Optional, Dict, Any
from app.config import settings
from app.core.metrics import CACHE_OPERATIONS_TOTAL


class URLCacheManager:
    """
    Manages Redis Cache-Aside with Anti-Penetration negative caching.
    Ensures hot URLs are served in <2ms while non-existent keys don't hammer PostgreSQL.
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _url_key(self, short_code: str) -> str:
        return f"url:{short_code}"

    def _negative_key(self, short_code: str) -> str:
        return f"url:not_found:{short_code}"

    async def get_url(self, short_code: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves cached URL metadata.
        Returns None on cache miss.
        """
        val = await self.redis.get(self._url_key(short_code))
        if val is not None:
            CACHE_OPERATIONS_TOTAL.labels(status="hit").inc()
            return json.loads(val)
        return None

    async def set_url(self, short_code: str, data: Dict[str, Any], ttl: int = settings.URL_CACHE_TTL_SECONDS) -> None:
        """
        Caches URL metadata with TTL.
        """
        await self.redis.setex(self._url_key(short_code), ttl, json.dumps(data))

    async def invalidate_url(self, short_code: str) -> None:
        """
        Invalidates cached entry upon URL deletion or deactivation.
        """
        await self.redis.delete(self._url_key(short_code))
        await self.redis.delete(self._negative_key(short_code))

    async def is_negative_cached(self, short_code: str) -> bool:
        """
        Checks if the short code is marked as known non-existent.
        Prevents DB hits for non-existent IDs.
        """
        exists = await self.redis.exists(self._negative_key(short_code))
        if exists:
            CACHE_OPERATIONS_TOTAL.labels(status="negative_hit").inc()
            return True
        return False

    async def set_negative_cache(self, short_code: str, ttl: int = settings.NEGATIVE_CACHE_TTL_SECONDS) -> None:
        """
        Marks short code as non-existent for a short TTL (anti-penetration).
        """
        await self.redis.setex(self._negative_key(short_code), ttl, "1")
