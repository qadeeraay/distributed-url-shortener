import time
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from app.config import settings
from app.db.session import get_db
from app.db.models import URL
from app.core.bloom import RedisBloomFilter
from app.core.cache import URLCacheManager, get_redis
from app.core.metrics import (
    URL_REDIRECT_LATENCY_SECONDS,
    CACHE_OPERATIONS_TOTAL,
    HTTP_REQUESTS_TOTAL
)

logger = logging.getLogger("url_shortener.redirect")
router = APIRouter(tags=["Redirect"])


@router.get("/r/{short_code}")
async def redirect_short_url(
    short_code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis)
):
    """
    Sub-5ms hot-path redirection handler.
    Checks Redis cache-aside and Bloom filter before falling back to PostgreSQL,
    and asynchronously dispatches clickstream events to Redis Streams.
    """
    start_time = time.perf_counter()
    destination_url = None
    cache_status = "miss"

    cache_mgr = URLCacheManager(redis_client)
    bloom = RedisBloomFilter(redis_client)

    # 1. Cache-aside lookup with anti-penetration guards
    try:
        cached_entry = await cache_mgr.get_url(short_code)
        if cached_entry:
            cache_status = "hit"
            expires_at_str = cached_entry.get("expires_at")
            if expires_at_str and datetime.now(timezone.utc) > datetime.fromisoformat(expires_at_str):
                await cache_mgr.invalidate_url(short_code)
                raise HTTPException(status_code=status.HTTP_410_GONE, detail="URL has expired.")

            if not cached_entry.get("is_active", True):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL is no longer active.")

            destination_url = cached_entry.get("original_url")
        elif await cache_mgr.is_negative_cached(short_code):
            cache_status = "negative_hit"
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found.")
        elif not await bloom.contains(short_code):
            cache_status = "bloom_filtered"
            CACHE_OPERATIONS_TOTAL.labels(status="bloom_filtered").inc()
            await cache_mgr.set_negative_cache(short_code)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found.")
    except HTTPException:
        raise
    except Exception as err:
        logger.warning("Cache evaluation failure, proceeding to persistent store: %s", err)

    # 2. Database query fallback
    if not destination_url:
        CACHE_OPERATIONS_TOTAL.labels(status="miss").inc()
        stmt = select(URL).where(URL.short_code == short_code)
        result = await db.execute(stmt)
        url_record = result.scalar_one_or_none()

        if not url_record or not url_record.is_active:
            try:
                await cache_mgr.set_negative_cache(short_code)
            except Exception:
                pass
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found.")

        if url_record.is_expired():
            try:
                await cache_mgr.invalidate_url(short_code)
            except Exception:
                pass
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="URL has expired.")

        destination_url = url_record.original_url

        try:
            await cache_mgr.set_url(
                short_code,
                {
                    "original_url": destination_url,
                    "is_active": url_record.is_active,
                    "expires_at": url_record.expires_at.isoformat() if url_record.expires_at else None
                }
            )
            await bloom.add(short_code)
        except Exception as err:
            logger.warning("Cache write-through failed for %s: %s", short_code, err)

    # 3. Non-blocking clickstream dispatch to Redis Streams
    client_ip = request.client.host if request.client else "127.0.0.1"
    click_data = {
        "short_code": short_code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ip": client_ip,
        "user_agent": request.headers.get("user-agent", "Unknown"),
        "referrer": request.headers.get("referer", "Direct")
    }

    try:
        await redis_client.xadd(
            settings.CLICKSTREAM_STREAM_KEY,
            {"payload": json.dumps(click_data)},
            maxlen=100_000
        )
    except Exception as err:
        logger.warning("Clickstream stream publishing skipped: %s", err)

    latency = time.perf_counter() - start_time
    URL_REDIRECT_LATENCY_SECONDS.labels(cache_status=cache_status).observe(latency)
    HTTP_REQUESTS_TOTAL.labels(method="GET", endpoint="/r/{short_code}", status=302).inc()

    return RedirectResponse(
        url=destination_url,
        status_code=settings.REDIRECT_HTTP_STATUS
    )

