"""Opaque keyset (cursor) pagination helpers.

Endpoints with a stable ``(sort_col, id)`` ordering can offer cursor pagination
*in addition to* offset pagination. The cursor encodes the last returned row's
sort key + id, so the next page can seek straight into the index instead of
scanning and discarding ``offset`` rows — the classic deep-page OFFSET cost.

The token is intentionally opaque (URL-safe base64 of JSON) so clients treat it
as a black box and we stay free to change the encoding later.
"""
from __future__ import annotations

import base64
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_


def encode_cursor(key: Any, last_id: UUID) -> str:
    """Encode the last row's ``key`` and ``id`` into an opaque token."""
    if isinstance(key, (datetime, date)):
        key = key.isoformat()
    payload = json.dumps({"k": key, "id": str(last_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[Any, UUID | None]:
    """Decode a cursor. Returns ``(key, uuid)`` or ``(None, None)`` on garbage."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        raw_id = data.get("id")
        return data.get("k"), (UUID(str(raw_id)) if raw_id else None)
    except Exception:
        return None, None


def parse_datetime_key(key: Any) -> datetime | None:
    """Coerce a decoded cursor key back into a ``datetime`` (or ``None``)."""
    if key is None:
        return None
    if isinstance(key, datetime):
        return key
    try:
        return datetime.fromisoformat(str(key))
    except Exception:
        return None


def keyset_desc(col, id_col, key_value: datetime, last_id: UUID):
    """WHERE predicate selecting rows strictly after ``(key_value, last_id)``
    in ``(col DESC, id DESC)`` order. Both columns must be non-nullable."""
    return or_(
        col < key_value,
        and_(col == key_value, id_col < last_id),
    )
