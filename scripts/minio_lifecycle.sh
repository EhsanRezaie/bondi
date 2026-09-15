#!/usr/bin/env bash
# Apply MinIO lifecycle rules for chat media (30-day expiry).
#
# Chat photos/voice are ephemeral by design — they expire after 30 days to keep
# storage bounded. Profile photos under `users/` are intentionally NOT touched.
#
# Run against a live stack:
#   ./scripts/minio_lifecycle.sh
set -euo pipefail

MINIO_USER="${MINIO_ROOT_USER:-bondi_minio}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-}"
EXPIRE_DAYS="${CHAT_MEDIA_EXPIRE_DAYS:-30}"
BUCKET="${S3_PRIVATE_BUCKET:-photos-private}"
NETWORK="${MINIO_NETWORK:-bondi_internal}"
ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"

docker run --rm --network "$NETWORK" minio/mc sh -c "
  mc alias set local '$ENDPOINT' '$MINIO_USER' '$MINIO_PASS' &&
  (mc ilm rule add --expire-days $EXPIRE_DAYS --prefix 'chat/photos/' local/$BUCKET || true) &&
  (mc ilm rule add --expire-days $EXPIRE_DAYS --prefix 'chat/voice/' local/$BUCKET || true) &&
  mc ilm rule ls local/$BUCKET
"
