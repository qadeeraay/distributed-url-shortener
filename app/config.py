from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Application Configuration
    APP_NAME: str = "Distributed URL Shortener & Analytics"
    DEBUG: bool = False
    API_V1_STR: str = "/api/v1"
    PORT: int = 8000
    BASE_URL: str = "http://localhost:8000"

    # PostgreSQL Database Configuration
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "url_shortener"
    DATABASE_URL: Optional[str] = None

    @property
    def async_database_url(self) -> str:
        if self.DATABASE_URL:
            if self.DATABASE_URL.startswith("postgresql://"):
                return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
            return self.DATABASE_URL
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None

    @property
    def redis_url(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # High-Throughput Base62 Settings
    # Offsets monotonic ID so codes start at length 6: 62^4 = 14,776,336
    BASE62_ID_OFFSET: int = 14_776_336

    # Caching & Anti-Penetration Parameters
    URL_CACHE_TTL_SECONDS: int = 86400  # 24 hours
    NEGATIVE_CACHE_TTL_SECONDS: int = 60  # 1 minute anti-penetration cache
    BLOOM_FILTER_NAME: str = "bloom:urls"
    BLOOM_FILTER_SIZE_BITS: int = 10_000_000  # ~1.2 MB bitset
    BLOOM_HASH_COUNT: int = 5

    # Redis Streams for Asynchronous Analytics Ingestion
    CLICKSTREAM_STREAM_KEY: str = "stream:url_clicks"
    CLICKSTREAM_CONSUMER_GROUP: str = "cg:analytics_workers"
    CLICKSTREAM_CONSUMER_NAME: str = "worker-1"

    # Redirect HTTP Status Code: 302 for real-time tracking, 301 for permanent
    REDIRECT_HTTP_STATUS: int = 302

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
