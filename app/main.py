import time
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db.session import init_db, engine
from app.api.router import api_router
from app.api.v1.redirect import router as redirect_router
from app.core.cache import get_redis_pool, close_redis_pool
from app.core.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    metrics_endpoint_response
)

logger = logging.getLogger("url_shortener")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        redis_client = await get_redis_pool()
        await redis_client.ping()
    except Exception as err:
        logger.warning("Initial Redis connection check deferred: %s", err)

    yield

    await close_redis_pool()
    await engine.dispose()


app = FastAPI(
    title="Distributed URL Shortener & Analytics Service",
    description="High-throughput URL shortener with low-latency redirection and real-time clickstream analytics.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
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


app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(redirect_router)


@app.get("/healthz", tags=["Observability"])
async def healthcheck():
    db_healthy = False
    redis_healthy = False

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_healthy = True
    except Exception:
        db_healthy = False

    try:
        redis_client = await get_redis_pool()
        await redis_client.ping()
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
    return metrics_endpoint_response()


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

