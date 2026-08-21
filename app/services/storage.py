"""Shared S3 client helpers.

A single reusable aioboto3.Session is created at import time. Clients are
cheap and connection-pooled, so every module reuses the same session instead
of constructing a fresh one per object upload.
"""

import json

import aioboto3
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("storage")

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
                                "Resource": [
                                    f"arn:aws:s3:::{settings.S3_PUBLIC_BUCKET}"
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": ["*"]},
                                "Action": ["s3:GetObject"],
                                "Resource": [
                                    f"arn:aws:s3:::{settings.S3_PUBLIC_BUCKET}/*"
                                ],
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