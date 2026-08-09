# app/core/metrics.py
"""Prometheus metrics for Bondi.

Exposes a FastAPI route at `/metrics` (Prometheus text format). The endpoint is
hidden from the OpenAPI schema and must only be reachable on the internal
network (nginx does not proxy it publicly).
"""
import time

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from app.core.logging import get_logger

logger = get_logger("metrics")

# --- HTTP request metrics ---------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# --- DB pool ----------------------------------------------------------------
DB_POOL_CHECKEDOUT = Gauge("bondi_db_pool_checked_out", "SQLAlchemy async pool checked-out connections")
DB_POOL_SIZE = Gauge("bondi_db_pool_size", "SQLAlchemy async pool total size")

# --- Redis ------------------------------------------------------------------
REDIS_PING_LATENCY = Histogram(
    "bondi_redis_ping_seconds", "Redis ping round-trip latency in seconds", buckets=(0.001, 0.005, 0.05)
)

# --- WebSocket ---------------------------------------------------------------
WS_ACTIVE_CONNECTIONS = Gauge("bondi_ws_active_connections", "Live WebSocket connections")

# --- Celery ------------------------------------------------------------------
CELERY_QUEUE_DEPTH = Gauge("bondi_celery_queue_depth", "Messages pending in the bondi Celery queue")


def record_request(method: str, path: str, status: int, duration: float):
    """Record one HTTP request (called from the metrics middleware)."""
    REQUEST_COUNT.labels(method=method, path=path, status=status).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(duration)


def update_db_pool(checkedout: int, total: int):
    DB_POOL_CHECKEDOUT.set(checkedout)
    DB_POOL_SIZE.set(total)


def update_ws_active(count: int):
    WS_ACTIVE_CONNECTIONS.set(count)


def update_celery_depth(depth: int):
    CELERY_QUEUE_DEPTH.set(depth)


def render() -> bytes:
    return generate_latest()