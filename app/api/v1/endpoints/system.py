# app/api/v1/endpoints/system.py
from fastapi import APIRouter, Depends, Request, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from datetime import datetime, timezone
from typing import Optional, Dict
import json
import os
import tempfile
from pathlib import Path

from app.db.session import get_session, engine
from app.core.redis import redis_client
from app.core.limiter import limiter
from app.core.config import settings
from app.core.cache import cache_get, cache_set, key_system_status, TTL_SYSTEM_STATUS
from app.schemas.system import (
    SystemStatusResponse,
    ServiceStatus,
    ServicesStatus,
    MaintenanceStatus,
    MaintenanceEnableRequest,
    MaintenanceEnableResponse,
    MaintenanceStatusResponse,
    VersionCheckResponse,
    VersionCheckRequest,
)

from app.core.logging import get_logger

logger = get_logger("system")

router = APIRouter(prefix="/system", tags=["system"])


# ---------------------------------------------------------------------------
# Maintenance Mode
# ---------------------------------------------------------------------------

MAINTENANCE_FILE = Path("maintenance.json")


async def get_maintenance_status() -> MaintenanceStatus:
    """Read maintenance status from file."""
    if not MAINTENANCE_FILE.exists():
        return MaintenanceStatus(
            enabled=False,
            message=None,
            start_time=None,
            end_time=None,
        )
    
    try:
        with open(MAINTENANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MaintenanceStatus(
            enabled=data.get("maintenance_mode", False),
            message=data.get("message"),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
        )
    except Exception:
        return MaintenanceStatus(
            enabled=False,
            message=None,
            start_time=None,
            end_time=None,
        )


async def set_maintenance_status(
    maintenance_mode: bool,
    message: Optional[str] = None
) -> MaintenanceStatus:
    """Set maintenance status."""
    data = MaintenanceStatus(
        enabled=maintenance_mode,
        message=message or ("System is currently under maintenance. Please try again later." if maintenance_mode else None),
        start_time=datetime.now(timezone.utc).isoformat() if maintenance_mode else None,
        end_time=None,
    )
    
    if maintenance_mode:
        data_dict = {
            "maintenance_mode": data.enabled,
            "message": data.message,
            "start_time": data.start_time,
            "end_time": data.end_time,
        }
        fd, tmp_path = tempfile.mkstemp(dir=str(MAINTENANCE_FILE.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, indent=2)
            os.replace(tmp_path, str(MAINTENANCE_FILE))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    else:
        if MAINTENANCE_FILE.exists():
            MAINTENANCE_FILE.unlink()
    
    return data


def version_compare(v1: str, v2: str) -> int:
    """
    Compare two version strings.
    Returns: 1 if v1 > v2, -1 if v1 < v2, 0 if equal
    """
    def normalize(v: str) -> tuple:
        return tuple(int(x) for x in v.split('.'))
    
    v1_parts = normalize(v1)
    v2_parts = normalize(v2)
    
    for i in range(max(len(v1_parts), len(v2_parts))):
        a = v1_parts[i] if i < len(v1_parts) else 0
        b = v2_parts[i] if i < len(v2_parts) else 0
        if a > b:
            return 1
        if a < b:
            return -1
    return 0


async def check_admin_auth(request: Request) -> bool:
    """Check if request has admin authentication."""
    admin_key = request.headers.get("X-Admin-Key")
    return admin_key and admin_key == settings.ADMIN_SECRET_KEY


# ---------------------------------------------------------------------------
# Public Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=SystemStatusResponse)
@limiter.limit("60/minute")
async def system_status(request: Request, response: Response) -> SystemStatusResponse:
    """
    System status endpoint for splash screen.
    Returns system availability, maintenance mode, and version info.
    """
    response.headers["Cache-Control"] = "public, max-age=60"
    cached = await cache_get(redis_client, key_system_status())
    if cached:
        return SystemStatusResponse(**cached)
    # Get maintenance status
    maintenance = await get_maintenance_status()
    
    # Check database connectivity
    db_status = "ok"
    db_latency = None
    try:
        async with engine.connect() as conn:
            start = datetime.now(timezone.utc)
            await conn.execute(text("SELECT 1"))
            db_latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    except Exception:
        db_status = "error"
    
    # Check Redis connectivity
    redis_status = "ok"
    redis_latency = None
    try:
        start = datetime.now(timezone.utc)
        await redis_client.ping()
        redis_latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    except Exception:
        redis_status = "error"
    
    # Check MinIO connectivity
    minio_status = "ok"
    try:
        from app.services.photo_service import PhotoService
        async with PhotoService._s3_client() as s3:
            await s3.head_bucket(Bucket=settings.S3_PRIVATE_BUCKET)
    except Exception:
        minio_status = "error"
    
    # Determine overall status
    overall_status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    
    result = SystemStatusResponse(
        status=overall_status,
        timestamp=datetime.now(timezone.utc).isoformat(),
        version=getattr(settings, "APP_VERSION", "1.0.0"),
        environment=getattr(settings, "ENVIRONMENT", "production"),
        services=ServicesStatus(
            database=ServiceStatus(
                status=db_status,
                latency_ms=round(db_latency, 2) if db_latency else None,
            ),
            redis=ServiceStatus(
                status=redis_status,
                latency_ms=round(redis_latency, 2) if redis_latency else None,
            ),
            storage=ServiceStatus(
                status=minio_status,
                latency_ms=None,
            ),
        ),
        maintenance=maintenance,
    )
    await cache_set(redis_client, key_system_status(), result.model_dump(mode='json'), TTL_SYSTEM_STATUS)
    return result


# ---------------------------------------------------------------------------
# Version Check Endpoint
# ---------------------------------------------------------------------------

@router.post("/version-check", response_model=VersionCheckResponse)
@limiter.limit("30/minute")
async def version_check(
    request: Request,
    body: VersionCheckRequest,
) -> VersionCheckResponse:
    """
    Check if the app version is compatible with the backend.
    Called on splash screen.
    """
    # Check maintenance first
    maintenance = await get_maintenance_status()
    if maintenance.enabled:
        return VersionCheckResponse(
            status="maintenance",
            message=maintenance.message or "System is under maintenance",
            current_version=body.version,
            minimum_version="",
            platform=body.platform,
            update_url=None,
            force_update=False,
        )
    
    # ✅ Get version override (runtime overrides)
    override = await get_version_override()
    
    # ✅ Get minimum version from settings, then override if present
    if body.platform == "android":
        min_version = override.get("minimum_versions", {}).get("android", settings.MIN_ANDROID_VERSION)
        update_url = settings.PLAY_STORE_URL
    else:
        min_version = override.get("minimum_versions", {}).get("ios", settings.MIN_IOS_VERSION)
        update_url = settings.APP_STORE_URL
    
    # ✅ Get force update from override or settings
    force_update = override.get("force_update", settings.FORCE_UPDATE_ENABLED)
    force_update_message = override.get("force_update_message", settings.FORCE_UPDATE_MESSAGE)
    
    # Compare versions
    compare_result = version_compare(body.version, min_version)
    
    if compare_result < 0:
        # Version is older than minimum - update required
        return VersionCheckResponse(
            status="update_required",
            message=force_update_message if force_update else "A new version is available. Please update to get the latest features.",
            current_version=body.version,
            minimum_version=min_version,
            platform=body.platform,
            update_url=update_url,
            force_update=force_update,
        )
    
    # Version is compatible
    return VersionCheckResponse(
        status="ok",
        message=None,
        current_version=body.version,
        minimum_version=min_version,
        platform=body.platform,
        update_url=None,
        force_update=False,
    )


# ---------------------------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------------------------

@router.post("/maintenance/enable", response_model=MaintenanceEnableResponse)
async def enable_maintenance(
    request: Request,
    body: MaintenanceEnableRequest,
) -> MaintenanceEnableResponse:
    """
    Enable maintenance mode.
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    data = await set_maintenance_status(True, body.message)
    return MaintenanceEnableResponse(
        status="ok",
        message="Maintenance mode enabled",
        data=data,
    )


@router.post("/maintenance/disable", response_model=MaintenanceEnableResponse)
async def disable_maintenance(request: Request) -> MaintenanceEnableResponse:
    """
    Disable maintenance mode.
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    data = await set_maintenance_status(False)
    return MaintenanceEnableResponse(
        status="ok",
        message="Maintenance mode disabled",
        data=data,
    )


@router.get("/maintenance/status", response_model=MaintenanceStatusResponse)
async def maintenance_status(request: Request) -> MaintenanceStatusResponse:
    """
    Get current maintenance status.
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    data = await get_maintenance_status()
    return MaintenanceStatusResponse(
        maintenance_mode=data.enabled,
        message=data.message,
        start_time=data.start_time,
        end_time=data.end_time,
    )


# ---------------------------------------------------------------------------
# Admin Version Control Endpoints (Override settings at runtime)
# ---------------------------------------------------------------------------

VERSION_OVERRIDE_FILE = Path("version_override.json")


async def get_version_override() -> Dict:
    """Read version override from file."""
    if not VERSION_OVERRIDE_FILE.exists():
        return {}
    
    try:
        with open(VERSION_OVERRIDE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


async def set_version_override(data: Dict) -> None:
    """Write version override to file atomically."""
    fd, tmp_path = tempfile.mkstemp(dir=str(VERSION_OVERRIDE_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(VERSION_OVERRIDE_FILE))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


@router.post("/version/set-minimum")
async def set_minimum_version(
    request: Request,
    platform: str,
    version: str,
) -> dict:
    """
    Set minimum required version for a platform (runtime override).
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    if platform not in ["android", "ios"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform must be 'android' or 'ios'",
        )
    
    override = await get_version_override()
    if "minimum_versions" not in override:
        override["minimum_versions"] = {}
    override["minimum_versions"][platform] = version
    
    await set_version_override(override)
    
    return {
        "status": "ok",
        "message": f"Minimum version for {platform} set to {version}",
        "data": override,
    }


@router.post("/version/force-update")
async def set_force_update(
    request: Request,
    force: bool,
    message: Optional[str] = None,
) -> dict:
    """
    Enable or disable force update.
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    override = await get_version_override()
    override["force_update"] = force
    if message:
        override["force_update_message"] = message
    
    await set_version_override(override)
    
    return {
        "status": "ok",
        "message": f"Force update {'enabled' if force else 'disabled'}",
        "data": override,
    }


@router.get("/version/config")
async def get_version_config(request: Request) -> dict:
    """
    Get current version configuration (settings + overrides).
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    override = await get_version_override()
    
    # Merge settings with overrides
    config = {
        "minimum_versions": {
            "android": override.get("minimum_versions", {}).get("android", settings.MIN_ANDROID_VERSION),
            "ios": override.get("minimum_versions", {}).get("ios", settings.MIN_IOS_VERSION),
        },
        "force_update": override.get("force_update", settings.FORCE_UPDATE_ENABLED),
        "force_update_message": override.get("force_update_message", settings.FORCE_UPDATE_MESSAGE),
        "app_version": settings.APP_VERSION,
        "play_store_url": settings.PLAY_STORE_URL,
        "app_store_url": settings.APP_STORE_URL,
    }
    
    return {
        "status": "ok",
        "data": config,
    }


@router.delete("/version/override")
async def clear_version_override(request: Request) -> dict:
    """
    Clear all version overrides (revert to settings).
    Admin only endpoint.
    """
    if not await check_admin_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    
    if VERSION_OVERRIDE_FILE.exists():
        VERSION_OVERRIDE_FILE.unlink()
    
    return {
        "status": "ok",
        "message": "Version overrides cleared. Using settings values.",
    }