from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("core.limiter")

# Uses Redis as backend so limits are shared across multiple workers
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    enabled=True,  # Can be disabled for tests via conftest.py
)
