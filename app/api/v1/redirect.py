import time
import json
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
from app.core.cache import URLCacheManager
from app.core.metrics import (
    URL_REDIRECT_LATENCY_SECONDS,
    CACHE_OPERATIONS_TOTAL,
    HTTP_REQUESTS_TOTAL
)

router = APIRouter(tags=["Redirect"])


def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


@router.get("/r/{short_code}")
async def redirect_short_url(
    short_code: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Sub-5ms Hot Path Redirect Handler.
    Execution Flow:
    1. Redis Cache lookup (O(1)) -> sub-millisecond redirect if hit.
    2. Negative cache check -> instantaneous 404 rejection.
    3. Bloom Filter verification -> eliminates DB cache penetration attacks.
    4. Database fallback on cache miss + populates Redis.
    5. Asynchronous clickstream event emitted to Redis Stream without blocking redirect.
    """
    start_time = time.perf_counter()
    destination_url = None
    cache_status = "miss"
    redis_available = True

    try:
        redis_client = get_redis()
    except Exception:
        redis_available = False
        redis_client = None

    try:
        if redis_available and redis_client:
            cache_mgr = URLCacheManager(redis_client)
            bloom = RedisBloomFilter(redis_client)

            # Step 1: Check Redis Cache-Aside
            cached_entry = await cache_mgr.get_url(short_code)
            if cached_entry:
                cache_status = "hit"
                expires_at_str = cached_entry.get("expires_at")
                if expires_at_str:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    if datetime.now(timezone.utc) > expires_at:
                        await cache_mgr.invalidate_url(short_code)
                        raise HTTPException(status_code=status.HTTP_410_GONE, detail="URL has expired.")

                if not cached_entry.get("is_active", True):
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL is no longer active.")

                destination_url = cached_entry["original_url"]

            # Step 2: Check Negative Cache (known 404s)
            elif await cache_mgr.is_negative_cached(short_code):
                cache_status = "negative_hit"
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found.")

            # Step 3: Check Bloom Filter
            elif not await bloom.contains(short_code):
                cache_status = "bloom_filtered"
                CACHE_OPERATIONS_TOTAL.labels(status="bloom_filtered").inc()
                await cache_mgr.set_negative_cache(short_code)
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found.")

        # Step 4: Database fallback if cache missed or redis was bypassed
        if not destination_url:
            CACHE_OPERATIONS_TOTAL.labels(status="miss").inc()
            stmt = select(URL).where(URL.short_code == short_code)
            result = await db.execute(stmt)
            url_obj = result.scalar_one_or_none()

            if not url_obj or not url_obj.is_active:
                if redis_available and redis_client:
                    await cache_mgr.set_negative_cache(short_code)
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found.")

            if url_obj.is_expired():
                if redis_available and redis_client:
                    await cache_mgr.invalidate_url(short_code)
                raise HTTPException(status_code=status.HTTP_410_GONE, detail="URL has expired.")

            destination_url = url_obj.original_url

            # Cache the result and add to Bloom filter
            if redis_available and redis_client:
                await cache_mgr.set_url(
                    short_code,
                    {
                        "original_url": destination_url,
                        "is_active": url_obj.is_active,
                        "expires_at": url_obj.expires_at.isoformat() if url_obj.expires_at else None
                    }
                )
                await bloom.add(short_code)

        # Step 5: Asynchronous event ingestion to Redis Stream
        # Does not block the HTTP redirect response
        if redis_available and redis_client:
            client_ip = request.client.host if request.client else "127.0.0.1"
            user_agent = request.headers.get("user-agent", "Unknown")
            referrer = request.headers.get("referer", "Direct")

            click_payload = {
                "short_code": short_code,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ip": client_ip,
                "user_agent": user_agent,
                "referrer": referrer
            }

            try:
                await redis_client.xadd(
                    settings.CLICKSTREAM_STREAM_KEY,
                    {"payload": json.dumps(click_payload)},
                    maxlen=100_000
                )
            except Exception:
                pass

    finally:
        if redis_available and redis_client:
            await redis_client.aclose()
        latency = time.perf_counter() - start_time
        URL_REDIRECT_LATENCY_SECONDS.labels(cache_status=cache_status).observe(latency)
        HTTP_REQUESTS_TOTAL.labels(method="GET", endpoint="/r/{short_code}", status=302).inc()

    return RedirectResponse(
        url=destination_url,
        status_code=settings.REDIRECT_HTTP_STATUS
    )
