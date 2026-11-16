# app/api/v1/endpoints/system.py
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.cache import TTL_SYSTEM_STATUS, cache_get, cache_set, key_system_status
from app.core.config import settings
from app.core.deps import AdminIdentity, get_admin_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.db.session import engine
from app.schemas.system import (
    MaintenanceEnableRequest,
    MaintenanceEnableResponse,
    MaintenanceStatus,
    MaintenanceStatusResponse,
    ServicesStatus,
    ServiceStatus,
    SystemStatusResponse,
    VersionCheckRequest,
    VersionCheckResponse,
)

logger = get_logger("system")

router = APIRouter(prefix="/system", tags=["system"])


# ---------------------------------------------------------------------------
# Maintenance Mode
# ---------------------------------------------------------------------------

MAINTENANCE_FILE = Path("maintenance.json")

# Phase 2.4: state moved to Redis (shared across workers); the JSON files remain
# as durable bootstrap/backup so state survives Redis flush or restart.
_MAINTENANCE_REDIS_KEY = "system:maintenance"
_VERSION_OVERRIDE_REDIS_KEY = "system:version_override"


def _read_file_json(path: Path) -> dict | None:
    """Blocking file read — call ONLY via run_in_threadpool."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("system_state_file_read_failed", file=path.name, error=str(e))
        return None


def _write_file_json(path: Path, data_dict: dict) -> None:
    """Blocking atomic file write — call ONLY via run_in_threadpool."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, indent=2)
        os.replace(tmp_path, str(path))
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        logger.error("system_state_file_write_failed", file=path.name, error=str(e), exc_info=True)
        raise


def _delete_file(path: Path) -> None:
    """Blocking file delete — call ONLY via run_in_threadpool."""
    path.unlink(missing_ok=True)


async def _redis_get(key: str) -> str | None:
    try:
        raw = await redis_client.get(key)
        return str(raw) if raw is not None else None
    except Exception as e:
        logger.warning("system_state_redis_read_failed", key=key, error=str(e))
        return None


async def _redis_set(key: str, value: str) -> None:
    try:
        await redis_client.set(key, value)
    except Exception as e:
        logger.warning("system_state_redis_write_failed", key=key, error=str(e))


async def _redis_delete(key: str) -> None:
    try:
        await redis_client.delete(key)
    except Exception as e:
        logger.warning("system_state_redis_delete_failed", key=key, error=str(e))


async def get_maintenance_status() -> MaintenanceStatus:
    """Read maintenance status — Redis primary, JSON file fallback."""
    raw = await _redis_get(_MAINTENANCE_REDIS_KEY)
    if raw is not None:
        try:
            data = json.loads(raw)
            return MaintenanceStatus(
                enabled=data.get("maintenance_mode", False),
                message=data.get("message"),
                start_time=data.get("start_time"),
                end_time=data.get("end_time"),
            )
        except Exception as e:
            logger.warning("maintenance_status_parse_failed", error=str(e), exc_info=True)

    # Redis miss/flush → bootstrap from the durable file (off the event loop).
    data_file = await run_in_threadpool(_read_file_json, MAINTENANCE_FILE)
    if data_file is not None:
        status = MaintenanceStatus(
            enabled=data_file.get("maintenance_mode", False),
            message=data_file.get("message"),
            start_time=data_file.get("start_time"),
            end_time=data_file.get("end_time"),
        )
        await _redis_set(_MAINTENANCE_REDIS_KEY, json.dumps(data_file))
        return status

    return MaintenanceStatus(enabled=False, message=None, start_time=None, end_time=None)


async def set_maintenance_status(
    maintenance_mode: bool, message: str | None = None
) -> MaintenanceStatus:
    """Set maintenance status — Redis primary, durable file backup."""
    data = MaintenanceStatus(
        enabled=maintenance_mode,
        message=message
        or (
            "System is currently under maintenance. Please try again later."
            if maintenance_mode
            else None
        ),
        start_time=datetime.now(UTC).isoformat() if maintenance_mode else None,
        end_time=None,
    )

    data_dict = {
        "maintenance_mode": data.enabled,
        "message": data.message,
        "start_time": data.start_time,
        "end_time": data.end_time,
    }
    encoded = json.dumps(data_dict)
    await _redis_set(_MAINTENANCE_REDIS_KEY, encoded)

    if maintenance_mode:
        await run_in_threadpool(_write_file_json, MAINTENANCE_FILE, data_dict)
    else:
        await run_in_threadpool(_delete_file, MAINTENANCE_FILE)

    return data


def version_compare(v1: str, v2: str) -> int:
    """
    Compare two version strings.
    Returns: 1 if v1 > v2, -1 if v1 < v2, 0 if equal
    """

    def normalize(v: str) -> tuple:
        return tuple(int(x) for x in v.split("."))

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
    """Legacy admin check via X-Admin-Key header (kept for reference).

    Routes below now use Depends(get_admin_user), which accepts both the
    admin JWT and the X-Admin-Key header, so this helper is deprecated.
    """
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
            start = datetime.now(UTC)
            await conn.execute(text("SELECT 1"))
            db_latency = (datetime.now(UTC) - start).total_seconds() * 1000
    except Exception as e:
        logger.warning("system_status_db_check_failed", error=str(e), exc_info=True)
        db_status = "error"

    # Check Redis connectivity
    redis_status = "ok"
    redis_latency = None
    try:
        start = datetime.now(UTC)
        await redis_client.ping()
        redis_latency = (datetime.now(UTC) - start).total_seconds() * 1000
    except Exception as e:
        logger.warning("system_status_redis_check_failed", error=str(e), exc_info=True)
        redis_status = "error"

    # Check MinIO connectivity
    minio_status = "ok"
    try:
        from app.services.photo_service import PhotoService

        async with PhotoService._s3_client() as s3:
            await s3.head_bucket(Bucket=settings.S3_PRIVATE_BUCKET)
    except Exception as e:
        logger.warning("system_status_minio_check_failed", error=str(e), exc_info=True)
        minio_status = "error"

    # Determine overall status
    overall_status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    result = SystemStatusResponse(
        status=overall_status,
        timestamp=datetime.now(UTC).isoformat(),
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
    await cache_set(
        redis_client, key_system_status(), result.model_dump(mode="json"), TTL_SYSTEM_STATUS
    )
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
        min_version = override.get("minimum_versions", {}).get(
            "android", settings.MIN_ANDROID_VERSION
        )
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
            message=force_update_message
            if force_update
            else "A new version is available. Please update to get the latest features.",
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
    body: MaintenanceEnableRequest,
    admin: AdminIdentity = Depends(get_admin_user),
) -> MaintenanceEnableResponse:
    """
    Enable maintenance mode.
    Admin only endpoint.
    """
    data = await set_maintenance_status(True, body.message)
    return MaintenanceEnableResponse(
        status="ok",
        message="Maintenance mode enabled",
        data=data,
    )


@router.post("/maintenance/disable", response_model=MaintenanceEnableResponse)
async def disable_maintenance(
    admin: AdminIdentity = Depends(get_admin_user),
) -> MaintenanceEnableResponse:
    """
    Disable maintenance mode.
    Admin only endpoint.
    """
    data = await set_maintenance_status(False)
    return MaintenanceEnableResponse(
        status="ok",
        message="Maintenance mode disabled",
        data=data,
    )


@router.get("/maintenance/status", response_model=MaintenanceStatusResponse)
async def maintenance_status(
    admin: AdminIdentity = Depends(get_admin_user),
) -> MaintenanceStatusResponse:
    """
    Get current maintenance status.
    Admin only endpoint.
    """
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


async def get_version_override() -> dict:
    """Read version override — Redis primary, JSON file fallback."""
    raw = await _redis_get(_VERSION_OVERRIDE_REDIS_KEY)
    if raw is not None:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("version_override_parse_failed", error=str(e), exc_info=True)

    # Redis miss/flush → bootstrap from the durable file (off the event loop).
    data_file = await run_in_threadpool(_read_file_json, VERSION_OVERRIDE_FILE)
    if data_file is not None:
        await _redis_set(_VERSION_OVERRIDE_REDIS_KEY, json.dumps(data_file))
        return data_file

    return {}


async def set_version_override(data: dict) -> None:
    """Write version override — Redis primary, durable file backup (atomic)."""
    await _redis_set(_VERSION_OVERRIDE_REDIS_KEY, json.dumps(data))
    await run_in_threadpool(_write_file_json, VERSION_OVERRIDE_FILE, data)


@router.post("/version/set-minimum")
async def set_minimum_version(
    platform: str,
    version: str,
    admin: AdminIdentity = Depends(get_admin_user),
) -> dict:
    """
    Set minimum required version for a platform (runtime override).
    Admin only endpoint.
    """
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
    force: bool,
    message: str | None = None,
    admin: AdminIdentity = Depends(get_admin_user),
) -> dict:
    """
    Enable or disable force update.
    Admin only endpoint.
    """
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
async def get_version_config(
    admin: AdminIdentity = Depends(get_admin_user),
) -> dict:
    """
    Get current version configuration (settings + overrides).
    Admin only endpoint.
    """
    override = await get_version_override()

    # Merge settings with overrides
    config = {
        "minimum_versions": {
            "android": override.get("minimum_versions", {}).get(
                "android", settings.MIN_ANDROID_VERSION
            ),
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
async def clear_version_override(
    admin: AdminIdentity = Depends(get_admin_user),
) -> dict:
    """
    Clear all version overrides (revert to settings).
    Admin only endpoint.
    """
    await _redis_delete(_VERSION_OVERRIDE_REDIS_KEY)
    await run_in_threadpool(_delete_file, VERSION_OVERRIDE_FILE)

    return {
        "status": "ok",
        "message": "Version overrides cleared. Using settings values.",
    }
