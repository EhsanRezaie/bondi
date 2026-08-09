"""Shared S3 client helpers.

A single reusable aioboto3.Session is created at import time. Clients are
cheap and connection-pooled, so every module reuses the same session instead
of constructing a fresh one per object upload.
"""

import aioboto3

from app.core.config import settings

_s3_session = aioboto3.Session()


def s3_client():
    """Return an async context-manager S3 client configured for MinIO/S3."""
    return _s3_session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    )