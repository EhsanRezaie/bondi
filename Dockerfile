# syntax=docker/dockerfile:1

# Thin app layer. Deps come from the pre-built bondi-base image (see
# Dockerfile.base + scripts/build-base.sh). Build this first or run
# `bash scripts/build-base.sh` before `docker compose build`.

FROM bondi-base:latest

WORKDIR /app

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["sh", "entrypoint.sh"]
