#!/bin/bash
set -e

# Build the dependency base image ONLY when requirements.txt changes.
# Tags: bondi-base:<md5(requirements.txt)> and bondi-base:latest.
# The <hash> tag lets the deploy script detect "nothing changed, skip".
# Safe to run manually on the server — reuse the existing image when deps are unchanged.

cd "$(dirname "$0")/.."

if [ ! -f requirements.txt ]; then
    echo "ERROR: requirements.txt not found in $(pwd)" >&2
    exit 1
fi

REQ_HASH=$(md5sum requirements.txt | cut -d' ' -f1)
HASH_TAG="bondi-base:$REQ_HASH"

if docker image inspect "$HASH_TAG" >/dev/null 2>&1; then
    echo ">>> Base image up to date ($HASH_TAG) — skipping pip install"
else
    echo ">>> requirements.txt changed — building base image ($HASH_TAG)..."
    DOCKER_BUILDKIT=1 docker build -f Dockerfile.base -t "$HASH_TAG" .
fi

docker tag "$HASH_TAG" bondi-base:latest
echo ">>> Base image ready: bondi-base:latest"
