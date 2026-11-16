from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("core.limiter")

# Uses Redis as backend so limits are shared across multiple workers
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    # Rate limiting can be disabled for load tests / local benchmarking via
    # RATELIMIT_ENABLED=false (a single test source IP would 429 immediately).
    enabled=settings.RATELIMIT_ENABLED,  # Can be disabled for tests via conftest.py
    # If Redis is down, fall back to per-process in-memory counting instead of
    # raising on EVERY authenticated request. Limits become per-worker during
    # the outage (weaker, not stronger) — acceptable degraded mode.
    in_memory_fallback_enabled=True,
)
# slowapi's constructor re-reads RATELIMIT_ENABLED from env/.env (its own
# reserved key) and OVERWRITES the constructor value with the raw string —
# bool("false") is True, so the knob silently did nothing. Re-assert our
# parsed setting after construction.
limiter.enabled = settings.RATELIMIT_ENABLED
