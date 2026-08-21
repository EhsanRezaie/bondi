from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import os
from contextlib import asynccontextmanager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from app.core.config import settings
from app.core.limiter import limiter
from app.core.metrics import (
    record_request,
    update_db_pool,
    update_ws_active,
    update_celery_depth,
    render as render_metrics,
)
from app.core.redis import redis_client

from app.db.session import engine, get_session
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.photos import router as photos_router  
from app.api.v1.endpoints.verify import router as verify_router
from app.api.v1.endpoints.test_face_verification import router as test_face_verification_router
from app.api.v1.endpoints.discover import router as discover_router
from app.api.v1.endpoints.swipes import router as swipes_router  
from app.api.v1.endpoints.search import router as search_router
from app.api.v1.endpoints.blocks import router as blocks_router
from app.api.v1.endpoints.rewards import router as rewards_router
from app.api.v1.endpoints.referrals import router as referrals_router
from app.api.v1.endpoints.subscriptions import router as subscription_router
from app.api.v1.endpoints.notifications import router as notifications_router
from app.api.v1.endpoints.reports import router as reports_router
from app.api.v1.endpoints.tickets import router as tickets_router
from app.api.v1.endpoints.admin_tickets import router as admin_tickets_router
from app.api.v1.endpoints.admin_reports import router as admin_reports_router
from app.api.v1.endpoints.admin_users import router as admin_users_router
from app.api.v1.endpoints.admin_dashboard import router as admin_dashboard_router
from app.api.v1.endpoints.admin_photos import router as admin_photos_router
from app.api.v1.endpoints.admin_announcements import router as admin_announcements_router
from app.api.v1.endpoints.locations import router as locations_router
from app.api.v1.endpoints.interests import router as interests_router
from app.api.v1.endpoints.prompts import router as prompts_router
from app.api.v1.endpoints.admin_messages import router as admin_messages_router
from app.api.v1.endpoints.admin_auth import router as admin_auth_router
from app.api.v1.endpoints.admin_logs import router as admin_logs_router
from app.api.v1.endpoints.system import router as admin_system_router


from app.api.v1.websocket.stream import router as websocket_router
from app.api.v1.endpoints.matches import router as matches_router
from app.api.v1.endpoints.messages import router as messages_router
from app.api.v1.endpoints.chats import router as chats_router

from app.core.logging import get_logger, setup_logging
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure MinIO/S3 buckets exist so photo/media uploads never 500 with
    # NoSuchBucket. Idempotent + non-fatal: a brief MinIO hiccup must not
    # block boot, and multi-worker starts race safely (create is guarded).
    try:
        from app.services.storage import ensure_buckets

        await ensure_buckets()
    except Exception:
        get_logger("app.main").exception("bucket bootstrap failed")

    yield


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    docs_url="/api/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/api/redoc" if settings.ENVIRONMENT == "development" else None,
    openapi_url="/api/openapi.json" if settings.ENVIRONMENT == "development" else None,
    lifespan=lifespan,
)

# Error tracking: structlog ERROR bridge -> GlitchTip + global exception handlers.
from app.core.error_handling import (
    http_context_middleware,
    register_exception_handlers,
    setup_sentry_handlers,
)
app.middleware("http")(http_context_middleware)
register_exception_handlers(app)
setup_sentry_handlers(app)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — mobile apps don't use CORS, but Swagger/Redoc UI does in dev
# Set CORS_ORIGINS in .env for production (e.g. "https://yourapp.ir,https://api.yourapp.ir")
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=bool(_cors_origins),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# GZip
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Prometheus request metrics (before routers; /metrics itself is excluded below)
from starlette.responses import Response


@app.middleware("http")
async def metrics_middleware(request, call_next):
    import time as _time
    start = _time.perf_counter()
    path = request.url.path
    response = await call_next(request)
    if path != "/metrics":
        record_request(request.method, path, response.status_code, _time.perf_counter() - start)
    return response


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    try:
        _pool = getattr(engine, "pool", None)
        if _pool is not None:
            update_db_pool(
                checkedout=_pool.checkedout(),
                total=_pool.total(),
            )
        from app.services.websocket_manager import websocket_manager
        update_ws_active(len(websocket_manager.active_connections))
        try:
            if redis_client:
                depth = 0
                try:
                    depth = int(await redis_client.llen("bondi") or 0)
                except Exception:
                    depth = 0
                update_celery_depth(depth)
        except Exception:
            pass
    except Exception:
        pass
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")

# Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(photos_router, prefix="/api/v1")  
app.include_router(verify_router, prefix="/api/v1")
app.include_router(test_face_verification_router, prefix="/api/v1")
app.include_router(discover_router, prefix="/api/v1")
app.include_router(swipes_router, prefix="/api/v1")  
app.include_router(search_router, prefix="/api/v1")
app.include_router(blocks_router, prefix="/api/v1")
app.include_router(matches_router, prefix="/api/v1")
app.include_router(messages_router, prefix="/api/v1")
app.include_router(chats_router, prefix="/api/v1")
app.include_router(rewards_router, prefix="/api/v1")
app.include_router(referrals_router, prefix="/api/v1")
app.include_router(subscription_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(tickets_router, prefix="/api/v1")
app.include_router(admin_tickets_router, prefix="/api/v1")
app.include_router(admin_reports_router, prefix="/api/v1")
app.include_router(admin_users_router, prefix="/api/v1")
app.include_router(admin_dashboard_router, prefix="/api/v1")
app.include_router(admin_photos_router, prefix="/api/v1")
app.include_router(admin_announcements_router, prefix="/api/v1")
app.include_router(locations_router, prefix="/api/v1")
app.include_router(interests_router, prefix="/api/v1")
app.include_router(prompts_router, prefix="/api/v1")
app.include_router(admin_messages_router, prefix="/api/v1")
app.include_router(admin_auth_router, prefix="/api/v1")
app.include_router(admin_logs_router, prefix="/api/v1")
app.include_router(admin_system_router, prefix="/api/v1")

# WebSocket Routers
app.include_router(websocket_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(session: AsyncSession = Depends(get_session)):
    await session.execute(select(1))
    await redis_client.ping()
    return {"status": "ready", "db": "ok", "redis": "ok"}