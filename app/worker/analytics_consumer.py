import asyncio
import json
import hashlib
import logging
from datetime import datetime, timezone
import user_agents
import redis.asyncio as aioredis
from sqlalchemy import select, update

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models import URL, ClickEvent, HourlyAnalytics
from app.core.metrics import ANALYTICS_EVENTS_PROCESSED

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AnalyticsWorker")

IP_SALT = "url_shortener_gdpr_salt_9981"


def hash_ip(ip: str) -> str:
    """Creates a GDPR-compliant irreversible pseudonymized hash of IP."""
    return hashlib.sha256(f"{ip}:{IP_SALT}".encode()).hexdigest()[:16]


def parse_user_agent(ua_string: str):
    """Extracts device, browser, and OS from user-agent string."""
    try:
        ua = user_agents.parse(ua_string)
        device = "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "Desktop" if ua.is_pc else "Bot" if ua.is_bot else "Other"
        browser = ua.browser.family or "Unknown"
        os = ua.os.family or "Unknown"
        return device, browser, os
    except Exception:
        return "Unknown", "Unknown", "Unknown"


class AnalyticsStreamConsumer:
    """
    Asynchronous Redis Streams consumer group worker.
    Processes click events from stream, resolves metadata, and persists to DB.
    """

    def __init__(self):
        self.stream_key = settings.CLICKSTREAM_STREAM_KEY
        self.group_name = settings.CLICKSTREAM_CONSUMER_GROUP
        self.consumer_name = settings.CLICKSTREAM_CONSUMER_NAME
        self.redis: aioredis.Redis = None
        self.running = True

    async def setup(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await self.redis.xgroup_create(
                self.stream_key,
                self.group_name,
                id="0",
                mkstream=True
            )
            logger.info(f"Consumer group '{self.group_name}' initialized on stream '{self.stream_key}'")
        except aioredis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"Consumer group '{self.group_name}' already exists.")
            else:
                logger.error(f"Consumer group init error: {e}")

    async def process_event(self, event_data: dict, session) -> None:
        short_code = event_data.get("short_code")
        timestamp_str = event_data.get("timestamp")
        ip = event_data.get("ip", "127.0.0.1")
        ua_str = event_data.get("user_agent", "")
        referrer = event_data.get("referrer", "Direct")

        clicked_at = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now(timezone.utc)
        device, browser, os = parse_user_agent(ua_str)
        ip_hash = hash_ip(ip)

        # 1. Insert ClickEvent record
        click_record = ClickEvent(
            short_code=short_code,
            clicked_at=clicked_at,
            ip_hash=ip_hash,
            country="Local" if ip in ("127.0.0.1", "localhost") else "US",
            city="Local" if ip in ("127.0.0.1", "localhost") else "Unknown",
            browser=browser,
            os=os,
            device=device,
            referrer=referrer if referrer else "Direct"
        )
        session.add(click_record)

        # 2. Increment URL total clicks count
        url_stmt = (
            update(URL)
            .where(URL.short_code == short_code)
            .values(clicks_count=URL.clicks_count + 1)
        )
        await session.execute(url_stmt)

        # 3. Aggregate into HourlyAnalytics rollup
        bucket_hour = clicked_at.replace(minute=0, second=0, microsecond=0)
        rollup_stmt = select(HourlyAnalytics).where(
            HourlyAnalytics.short_code == short_code,
            HourlyAnalytics.bucket_hour == bucket_hour
        )
        existing_rollup = (await session.execute(rollup_stmt)).scalar_one_or_none()

        if existing_rollup:
            existing_rollup.clicks += 1
        else:
            new_rollup = HourlyAnalytics(
                short_code=short_code,
                bucket_hour=bucket_hour,
                clicks=1,
                unique_visitors=1
            )
            session.add(new_rollup)

    async def run(self):
        await self.setup()
        logger.info(f"Analytics Worker '{self.consumer_name}' started and listening for clickstream events...")

        while self.running:
            try:
                response = await self.redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=20,
                    block=1500
                )

                if not response:
                    continue

                for stream, messages in response:
                    async with AsyncSessionLocal() as session:
                        msg_ids_to_ack = []
                        for msg_id, fields in messages:
                            try:
                                payload_str = fields.get("payload")
                                if payload_str:
                                    event_data = json.loads(payload_str)
                                    await self.process_event(event_data, session)
                                    ANALYTICS_EVENTS_PROCESSED.labels(status="success").inc()
                                msg_ids_to_ack.append(msg_id)
                            except Exception as ex:
                                logger.error(f"Failed processing click event {msg_id}: {ex}")
                                ANALYTICS_EVENTS_PROCESSED.labels(status="error").inc()

                        await session.commit()

                        if msg_ids_to_ack:
                            await self.redis.xack(self.stream_key, self.group_name, *msg_ids_to_ack)

            except asyncio.CancelledError:
                logger.info("Analytics Worker received shutdown signal.")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Error in analytics consumer loop: {e}")
                await asyncio.sleep(1)

        if self.redis:
            await self.redis.aclose()


if __name__ == "__main__":
    consumer = AnalyticsStreamConsumer()
    try:
        asyncio.run(consumer.run())
    except KeyboardInterrupt:
        logger.info("Analytics consumer terminated by user.")
