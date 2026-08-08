# app/core/error_handling.py
"""Centralized error logging + request-context helpers.

Policy (see plan):
- ERROR  (``exc_info=True``) -> structlog JSON + GlitchTip (via Sentry LoggingIntegration).
- WARNING                     -> structlog JSON only (expected client/duplicate/infra-noise).
- Unhandled request exceptions are converted to 500 JSON by the global handler in main.py.
"""

import contextvars
import logging
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

# NOTE: We intentionally do NOT call sentry_sdk.capture_exception() from blocks
# that also log at ERROR. The Sentry LoggingIntegration captures every
# structlog ERROR (with exc_info) as a GlitchTip event, so an extra explicit
# capture would produce duplicate events for the same bug.

logger = get_logger("error_handling")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")


def bind_request_context(request: Request) -> None:
    """Bind request_id and (best-effort) user_id into structlog contextvars."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request_id_var.set(request_id)
    structlog.contextvars.clear_contextvars()

    user_id = "-"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from app.core.security import decode_access_token
            decoded = decode_access_token(auth_header[7:])
            if decoded:
                user_id = decoded
        except Exception:
            logger.debug("user_id_bind_failed", exc_info=True)
    user_id_var.set(user_id)

    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        user_id=user_id,
        path=request.url.path,
    )


async def http_context_middleware(request: Request, call_next):
    """Attach request context to all logs for the duration of the request."""
    bind_request_context(request)
    try:
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id_var.get())
        return response
    except Exception:
        raise


def register_exception_handlers(app: FastAPI) -> None:
    """Wire global HTTP + validation error handlers so nothing 500s silently."""

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        structlog.contextvars.bind_contextvars(path=request.url.path)
        logger.exception(
            "unhandled_exception",
            error=repr(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
            headers={"X-Request-ID": request_id_var.get()},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            "request_validation_failed",
            errors=[str(e) for e in exc.errors()],
        )
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(exc.errors())},
            headers={"X-Request-ID": request_id_var.get()},
        )


def setup_sentry_handlers(app: FastAPI) -> None:
    """Bind the structlog ERROR bridge + global exceptions to sentry if DSN is set."""
    from app.core.config import settings

    if not settings.GLITCHTIP_DSN or settings.TESTING:
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.scope import Scope

    def before_send(event, hint):
        exc_info = hint.get("exc_info")
        if exc_info:
            etype = exc_info[0]
            # Expected client errors -> never deliver to GlitchTip.
            if issubclass(etype, (StarletteHTTPException, RequestValidationError)):
                return None
        return event

    sentry_sdk.init(
        dsn=settings.GLITCHTIP_DSN,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
            ),
        ],
        traces_sample_rate=0.1,
        environment=settings.ENVIRONMENT,
        send_default_pii=False,
        before_send=before_send,
    )