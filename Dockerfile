# syntax=docker/dockerfile:1

# Pin base image digest so the python/apt/pip layers stay cached across deploys
# (the floating "python:3.11-slim" tag invalidates the whole cache whenever it updates).
FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

WORKDIR /app

# System libraries needed by OpenCV, InsightFace, ONNX Runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Mount a persistent pip cache directory so downloaded wheels are reused across
# builds (avoids re-downloading onnxruntime/insightface/opencv every deploy).
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["sh", "entrypoint.sh"]
