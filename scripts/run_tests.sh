#!/usr/bin/env bash
# run_tests.sh — spin up test infra (docker compose), wait for healthy DB/Redis/MinIO,
# run pytest, then tear the test containers down.
#
# Usage:
#   ./run_tests.sh                          # full suite
#   ./run_tests.sh tests/test_auth.py -q    # pass-through pytest args
#   KEEP=1 ./run_tests.sh                   # don't tear down after (debugging)
#
# Volumes are kept across runs (only containers are removed), so the next run
# skips the slow first-time init of the test database/minio.

set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.test.yml"
LOG="${TEST_LOG:-/tmp/opencode/test-output.log}"

if [ ! -x .venv/bin/python ]; then
    echo "error: .venv not found — run 'uv sync' first" >&2
    exit 1
fi

echo "==> starting test infra (db:5433 redis:6380 minio:9090)"
# --wait only on the long-running services; the *_init one-shots exit 0 by
# design and would make `--wait` hang until timeout.
docker compose -f "$COMPOSE_FILE" up -d db_test redis_test minio-test --wait --wait-timeout 120
echo "==> infra healthy"

echo "==> running tests (output also written to $LOG)"
mkdir -p "$(dirname "$LOG")"
# args = pytest args, defaulting to the whole suite
if [ "$#" -gt 0 ]; then
    .venv/bin/python -m pytest "$@" 2>&1 | tee "$LOG"
else
    .venv/bin/python -m pytest tests/ 2>&1 | tee "$LOG"
fi
PYTEST_EXIT=${PIPESTATUS[0]}

if [ "${KEEP:-0}" != "1" ]; then
    echo "==> tearing down test infra"
    docker compose -f "$COMPOSE_FILE" down
fi

exit "$PYTEST_EXIT"
