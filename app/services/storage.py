"""Shared S3 client helpers.

A single reusable aioboto3.Session is created at import time. Clients are
cached per event loop: the first call enters the client context and keeps it
open, so its HTTP connection pool (TCP keep-alive to MinIO) is reused across
every later call instead of paying connect+TLS per request and re-scanning
botocore loaders. Exiting the context manager deliberately does NOT close the
cached client; it lives as long as its event loop.
"""

import asyncio
import json
import threading
import weakref
from contextlib import asynccontextmanager

import aioboto3
import botocore.config
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("storage")

_s3_session = aioboto3.Session()

# Per-event-loop cache of *entered* clients (weak keys: loop death drops the
# entry). Values are asyncio Futures so concurrent first-time callers on the
# same loop share one creation instead of racing two clients.
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Future]" = (
    weakref.WeakKeyDictionary()
)
_lock = threading.Lock()


async def _create_client():
    # Explicit timeouts + standard retries: without this a dead MinIO hangs
    # every upload/download for the botocore default 60s connect/read.
    cm = _s3_session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
        config=botocore.config.Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
            max_pool_connections=20,
        ),
    )
    return await cm.__aenter__()


@asynccontextmanager
async def s3_client():
    """Yield the event loop's shared S3 client (created on first use)."""
    loop = asyncio.get_running_loop()
    with _lock:
        fut = _clients.get(loop)
        if fut is None:
            fut = loop.create_future()
            _clients[loop] = fut
            owner = True
        else:
            owner = False
    if owner:
        try:
            client = await _create_client()
        except BaseException:
            with _lock:
                _clients.pop(loop, None)
            fut.cancel()  # wake any concurrent waiters; next call starts fresh
            raise
        fut.set_result(client)
    # Concurrent waiters on the same loop resolve here; a failed creation
    # raises in every waiter and the next call starts fresh.
    client = await fut
    yield client


def _missing_bucket(error: ClientError) -> bool:
    code = (error.response.get("Error") or {}).get("Code", "")
    return code in ("404", "NoSuchBucket")


async def ensure_buckets() -> None:
    """Idempotently create the configured buckets and make the public bucket
    anonymously readable.

    Called on every app boot so photo/media uploads never fail with
    ``NoSuchBucket`` when a MinIO volume is fresh or was reset — this no
    longer depends on the one-shot ``minio-init`` compose container.
    """
    async with s3_client() as s3:
        for bucket in (settings.S3_PRIVATE_BUCKET, settings.S3_PUBLIC_BUCKET):
            try:
                await s3.head_bucket(Bucket=bucket)
            except ClientError as e:
                if not _missing_bucket(e):
                    logger.warning("bucket_head_failed", bucket=bucket, error=str(e))
                    raise
                try:
                    await s3.create_bucket(Bucket=bucket)
                    logger.info("bucket_created", bucket=bucket)
                except ClientError as ce:
                    if not _missing_bucket(ce) and (ce.response.get("Error") or {}).get(
                        "Code", ""
                    ) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists", "409"):
                        raise

        # Mirror `mc anonymous set download`: anonymous s3:GetObject on public bucket.
        try:
            await s3.put_bucket_policy(
                Bucket=settings.S3_PUBLIC_BUCKET,
                Policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": ["*"]},
                                "Action": ["s3:GetBucketLocation"],
                                "Resource": [f"arn:aws:s3:::{settings.S3_PUBLIC_BUCKET}"],
                            },
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": ["*"]},
                                "Action": ["s3:GetObject"],
                                "Resource": [f"arn:aws:s3:::{settings.S3_PUBLIC_BUCKET}/*"],
                            },
                        ],
                    }
                ),
            )
            logger.info("bucket_policy_set", bucket=settings.S3_PUBLIC_BUCKET)
        except ClientError as e:
            logger.warning(
                "bucket_policy_set_failed",
                bucket=settings.S3_PUBLIC_BUCKET,
                error=str(e),
            )
