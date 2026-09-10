from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Text,
    Boolean,
    DateTime,
    Integer,
    Index
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class URL(Base):
    """
    Core URL mapping entity.
    `id` sequence acts as the monotonic distributed counter driving Base62 generation.
    `short_code` has a unique B-tree index for O(1) lookup.
    """
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_code = Column(String(32), unique=True, nullable=False, index=True)
    original_url = Column(Text, nullable=False)
    is_custom = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    clicks_count = Column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        Index("idx_urls_active_expires", "is_active", "expires_at"),
        Index("idx_urls_created_at", "created_at"),
    )

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class ClickEvent(Base):
    """
    Raw immutable click event ingested asynchronously from Redis Streams.
    Stored for granular analytics audits without blocking the redirect hot path.
    """
    __tablename__ = "click_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_code = Column(String(32), nullable=False, index=True)
    clicked_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    ip_hash = Column(String(64), nullable=True)  # Salted pseudonymized hash for GDPR compliance
    country = Column(String(64), default="Unknown", nullable=False)
    city = Column(String(64), default="Unknown", nullable=False)
    browser = Column(String(64), default="Unknown", nullable=False)
    os = Column(String(64), default="Unknown", nullable=False)
    device = Column(String(32), default="Unknown", nullable=False)
    referrer = Column(String(255), default="Direct", nullable=False)

    __table_args__ = (
        Index("idx_click_events_code_time", "short_code", "clicked_at"),
    )


class HourlyAnalytics(Base):
    """
    Pre-aggregated analytics rollup bucketed by hour.
    Ensures O(1) reads for analytical dashboards even with millions of clicks.
    """
    __tablename__ = "hourly_analytics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_code = Column(String(32), nullable=False, index=True)
    bucket_hour = Column(DateTime(timezone=True), nullable=False)
    clicks = Column(Integer, default=0, nullable=False)
    unique_visitors = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_hourly_rollup", "short_code", "bucket_hour", unique=True),
    )
