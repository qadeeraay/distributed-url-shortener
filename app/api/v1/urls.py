import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

logger = logging.getLogger("url_shortener.api")

from app.config import settings
from app.db.session import get_db
from app.db.models import URL, ClickEvent, HourlyAnalytics
from app.schemas.url import (
    URLCreateRequest,
    URLResponse,
    URLAnalyticsResponse,
    TimelinePoint
)
from app.core.base62 import encode, validate_custom_alias
from app.core.bloom import RedisBloomFilter
from app.core.cache import URLCacheManager
import redis.asyncio as aioredis

router = APIRouter(prefix="/urls", tags=["URLs"])


def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


@router.post("", response_model=URLResponse, status_code=status.HTTP_201_CREATED)
async def create_short_url(
    payload: URLCreateRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a shortened URL:
    - If custom_alias is provided: validates format, reserves slug, checks collision.
    - If no alias: generates collision-free Base62 code over monotonic DB sequence.
    - Updates Redis Bloom Filter and caches entry to prevent cache penetration.
    """
    redis_client = get_redis()
    cache_mgr = URLCacheManager(redis_client)
    bloom = RedisBloomFilter(redis_client)

    expires_at = None
    if payload.expires_in_hours:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)

    target_url_str = str(payload.url)

    if payload.custom_alias:
        alias = payload.custom_alias.strip()
        if not validate_custom_alias(alias):
            await redis_client.aclose()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom alias contains invalid characters or is a reserved keyword."
            )

        # Check collision in DB
        stmt = select(URL).where(URL.short_code == alias)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            await redis_client.aclose()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"The alias '{alias}' is already in use. Please choose another."
            )

        new_url = URL(
            short_code=alias,
            original_url=target_url_str,
            is_custom=True,
            expires_at=expires_at
        )
        db.add(new_url)
        await db.flush()
        short_code = alias
    else:
        # Monotonic sequence generation: Insert temporary record to allocate sequence ID
        temp_code = f"tmp_{datetime.now(timezone.utc).timestamp()}"
        new_url = URL(
            short_code=temp_code,
            original_url=target_url_str,
            is_custom=False,
            expires_at=expires_at
        )
        db.add(new_url)
        await db.flush()

        # Bijective Base62 encoding over sequence ID + offset
        short_code = encode(new_url.id + settings.BASE62_ID_OFFSET)
        new_url.short_code = short_code
        await db.flush()

    # Add to Bloom filter and cache
    try:
        await bloom.add(short_code)
        await cache_mgr.set_url(
            short_code,
            {
                "original_url": target_url_str,
                "is_active": True,
                "expires_at": expires_at.isoformat() if expires_at else None
            }
        )
    except Exception:
        # If redis is temporarily unavailable, DB write succeeds
        pass
    finally:
        await redis_client.aclose()

    return URLResponse(
        short_code=short_code,
        short_url=f"{settings.BASE_URL}/r/{short_code}",
        original_url=new_url.original_url,
        is_custom=new_url.is_custom,
        created_at=new_url.created_at,
        expires_at=new_url.expires_at,
        clicks_count=new_url.clicks_count
    )


@router.get("/{short_code}", response_model=URLResponse)
async def get_url_metadata(
    short_code: str,
    db: AsyncSession = Depends(get_db)
):
    """Retrieves URL metadata by short code."""
    stmt = select(URL).where(URL.short_code == short_code, URL.is_active == True)
    result = await db.execute(stmt)
    url_obj = result.scalar_one_or_none()

    if not url_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="URL not found or has been deactivated."
        )

    return URLResponse(
        short_code=url_obj.short_code,
        short_url=f"{settings.BASE_URL}/r/{url_obj.short_code}",
        original_url=url_obj.original_url,
        is_custom=url_obj.is_custom,
        created_at=url_obj.created_at,
        expires_at=url_obj.expires_at,
        clicks_count=url_obj.clicks_count
    )


@router.get("/{short_code}/analytics", response_model=URLAnalyticsResponse)
async def get_url_analytics(
    short_code: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns rich analytics for a short URL:
    - Total clicks and unique visitor counts
    - Referrer breakdown
    - Country / Geo breakdown
    - Browser & OS distributions
    - Last 24-hour hourly timeseries
    """
    stmt = select(URL).where(URL.short_code == short_code)
    result = await db.execute(stmt)
    url_obj = result.scalar_one_or_none()

    if not url_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="URL not found."
        )

    # Unique visitors count
    unique_stmt = select(func.count(func.distinct(ClickEvent.ip_hash))).where(
        ClickEvent.short_code == short_code
    )
    unique_visitors = (await db.execute(unique_stmt)).scalar() or 0

    # Top referrers
    ref_stmt = (
        select(ClickEvent.referrer, func.count(ClickEvent.id))
        .where(ClickEvent.short_code == short_code)
        .group_by(ClickEvent.referrer)
        .order_by(desc(func.count(ClickEvent.id)))
        .limit(10)
    )
    top_referrers = dict((await db.execute(ref_stmt)).all())

    # Top countries
    geo_stmt = (
        select(ClickEvent.country, func.count(ClickEvent.id))
        .where(ClickEvent.short_code == short_code)
        .group_by(ClickEvent.country)
        .order_by(desc(func.count(ClickEvent.id)))
        .limit(10)
    )
    top_countries = dict((await db.execute(geo_stmt)).all())

    # Browsers
    browser_stmt = (
        select(ClickEvent.browser, func.count(ClickEvent.id))
        .where(ClickEvent.short_code == short_code)
        .group_by(ClickEvent.browser)
        .order_by(desc(func.count(ClickEvent.id)))
        .limit(5)
    )
    browsers = dict((await db.execute(browser_stmt)).all())

    # OS / Platforms
    os_stmt = (
        select(ClickEvent.os, func.count(ClickEvent.id))
        .where(ClickEvent.short_code == short_code)
        .group_by(ClickEvent.os)
        .order_by(desc(func.count(ClickEvent.id)))
        .limit(5)
    )
    platforms = dict((await db.execute(os_stmt)).all())

    # Devices
    device_stmt = (
        select(ClickEvent.device, func.count(ClickEvent.id))
        .where(ClickEvent.short_code == short_code)
        .group_by(ClickEvent.device)
        .order_by(desc(func.count(ClickEvent.id)))
        .limit(5)
    )
    devices = dict((await db.execute(device_stmt)).all())

    # Last 24 hours timeline
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    timeline_stmt = (
        select(HourlyAnalytics)
        .where(
            HourlyAnalytics.short_code == short_code,
            HourlyAnalytics.bucket_hour >= since
        )
        .order_by(HourlyAnalytics.bucket_hour.asc())
    )
    rollups = (await db.execute(timeline_stmt)).scalars().all()
    timeline = [
        TimelinePoint(
            timestamp=r.bucket_hour.isoformat(),
            clicks=r.clicks,
            unique_visitors=r.unique_visitors
        )
        for r in rollups
    ]

    return URLAnalyticsResponse(
        short_code=short_code,
        original_url=url_obj.original_url,
        total_clicks=url_obj.clicks_count,
        unique_visitors=unique_visitors,
        top_referrers=top_referrers,
        top_countries=top_countries,
        browsers=browsers,
        platforms=platforms,
        devices=devices,
        timeline_last_24h=timeline
    )


@router.delete("/{short_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_short_url(
    short_code: str,
    db: AsyncSession = Depends(get_db)
):
    """Soft-deletes URL and immediately purges from Redis cache."""
    stmt = select(URL).where(URL.short_code == short_code)
    result = await db.execute(stmt)
    url_obj = result.scalar_one_or_none()

    if not url_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="URL not found."
        )

    url_obj.is_active = False
    await db.flush()

    try:
        redis_client = get_redis()
        cache_mgr = URLCacheManager(redis_client)
        await cache_mgr.invalidate_url(short_code)
        await redis_client.aclose()
    except Exception as e:
        logger.warning("Cache invalidation failed for URL ID %d: %s", url_obj.id, e)
