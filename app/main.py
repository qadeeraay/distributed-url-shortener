import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as aioredis
from sqlalchemy import text

from app.config import settings
from app.db.session import init_db, engine
from app.api.router import api_router
from app.api.v1.redirect import router as redirect_router
from app.core.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    metrics_endpoint_response
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager handling startup and shutdown events."""
    # Startup: Ensure database tables are provisioned
    await init_db()

    # Verify Redis connectivity
    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
    except Exception as e:
        print(f"Notice: Redis connection pending ({e})")

    yield

    # Shutdown: Release database connection pool
    await engine.dispose()


app = FastAPI(
    title="Production-Grade Distributed URL Shortener & Analytics",
    description=(
        "High-performance, distributed URL shortener designed to demonstrate system design excellence.\n\n"
        "### Key System Capabilities:\n"
        "- **Zero-Collision Key Generation:** Bijective Base62 encoding mapped over 64-bit sequences\n"
        "- **Sub-5ms Hot Path:** Redis Cache-Aside + Bitset Bloom Filter anti-cache penetration\n"
        "- **Decoupled Asynchronous Analytics:** Event-driven ingestion pipeline via Redis Streams\n"
        "- **Pre-Aggregated Analytics Rollups:** O(1) reads for time-series and geographic breakdowns\n"
        "- **Prometheus Observability:** Native latency histograms, request counters, and cache hit ratios"
    ),
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Observability middleware recording duration and status codes."""
    start_time = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start_time

    path = request.url.path
    if path.startswith("/r/"):
        path = "/r/{short_code}"
    elif path.startswith("/api/v1/urls/"):
        path = "/api/v1/urls/{short_code}"

    HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, endpoint=path).observe(duration)
    HTTP_REQUESTS_TOTAL.labels(method=request.method, endpoint=path, status=response.status_code).inc()

    return response


# Register Routers
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(redirect_router)


@app.get("/healthz", tags=["Observability"])
async def healthcheck():
    """Liveness & Readiness probe verifying PostgreSQL and Redis health."""
    db_healthy = False
    redis_healthy = False

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_healthy = True
    except Exception:
        db_healthy = False

    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        redis_healthy = True
    except Exception:
        redis_healthy = False

    is_healthy = db_healthy and redis_healthy
    resp_code = status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=resp_code,
        content={
            "status": "UP" if is_healthy else "DEGRADED",
            "services": {
                "database": "UP" if db_healthy else "DOWN",
                "redis": "UP" if redis_healthy else "DOWN"
            },
            "timestamp": time.time()
        }
    )


@app.get("/metrics", tags=["Observability"])
async def get_metrics():
    """Prometheus metrics scraping endpoint."""
    return metrics_endpoint_response()


@app.get("/", include_in_schema=False)
async def root():
    """Redirects base path to interactive OpenAPI documentation."""
    return RedirectResponse(url="/docs")
