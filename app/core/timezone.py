"""Time helpers — single source of truth for timezone-aware datetimes.

All datetime values persisted to tz-aware columns must be UTC (aware).
Daily-limit keys/counters use the Tehran calendar day (product decision:
limits reset at Tehran midnight, regardless of the user's travel timezone).
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")


def utcnow() -> datetime:
    """Aware, UTC-normalized now — always safe to store in tz columns."""
    return datetime.now(timezone.utc)


def _tehran_now() -> datetime:
    return datetime.now(TEHRAN)


def tehran_today() -> "datetime.date":
    return _tehran_now().date()


def tehran_date_key() -> str:
    return _tehran_now().strftime("%Y-%m-%d")


def seconds_until_tehran_midnight() -> int:
    now = _tehran_now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - now).total_seconds())