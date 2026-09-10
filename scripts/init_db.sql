-- Distributed URL Shortener Schema Initialization
CREATE TABLE IF NOT EXISTS urls (
    id BIGSERIAL PRIMARY KEY,
    short_code VARCHAR(32) NOT NULL UNIQUE,
    original_url TEXT NOT NULL,
    is_custom BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    expires_at TIMESTAMPTZ NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    clicks_count BIGINT DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code);
CREATE INDEX IF NOT EXISTS idx_urls_active_expires ON urls(is_active, expires_at);
CREATE INDEX IF NOT EXISTS idx_urls_created_at ON urls(created_at);

CREATE TABLE IF NOT EXISTS click_events (
    id BIGSERIAL PRIMARY KEY,
    short_code VARCHAR(32) NOT NULL,
    clicked_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    ip_hash VARCHAR(64) NULL,
    country VARCHAR(64) DEFAULT 'Unknown' NOT NULL,
    city VARCHAR(64) DEFAULT 'Unknown' NOT NULL,
    browser VARCHAR(64) DEFAULT 'Unknown' NOT NULL,
    os VARCHAR(64) DEFAULT 'Unknown' NOT NULL,
    device VARCHAR(32) DEFAULT 'Unknown' NOT NULL,
    referrer VARCHAR(255) DEFAULT 'Direct' NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_click_events_code_time ON click_events(short_code, clicked_at);

CREATE TABLE IF NOT EXISTS hourly_analytics (
    id BIGSERIAL PRIMARY KEY,
    short_code VARCHAR(32) NOT NULL,
    bucket_hour TIMESTAMPTZ NOT NULL,
    clicks INT DEFAULT 0 NOT NULL,
    unique_visitors INT DEFAULT 0 NOT NULL,
    CONSTRAINT uq_hourly_rollup UNIQUE (short_code, bucket_hour)
);

CREATE INDEX IF NOT EXISTS idx_hourly_short_code ON hourly_analytics(short_code);
