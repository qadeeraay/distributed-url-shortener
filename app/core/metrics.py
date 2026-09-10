from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Request Latency Histogram
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.001, 0.003, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)

# Total HTTP Requests Counter
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status"]
)

# Redirect Latency specifically for the hot path
URL_REDIRECT_LATENCY_SECONDS = Histogram(
    "url_redirect_latency_seconds",
    "Redirect endpoint latency in seconds",
    ["cache_status"],
    buckets=[0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1]
)

# Cache Hit/Miss & Negative Cache counters
CACHE_OPERATIONS_TOTAL = Counter(
    "cache_operations_total",
    "Total cache lookups by status",
    ["status"]  # 'hit', 'miss', 'negative_hit', 'bloom_filtered'
)

# Active URL Count
ACTIVE_URLS_GAUGE = Gauge(
    "active_urls_total",
    "Total active shortened URLs in system"
)

# Clickstream Analytics Pipeline Worker metrics
ANALYTICS_EVENTS_PROCESSED = Counter(
    "analytics_events_processed_total",
    "Clickstream events processed by background worker",
    ["status"]  # 'success', 'error'
)


def metrics_endpoint_response() -> Response:
    """Generates Prometheus scrapable metrics payload."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
