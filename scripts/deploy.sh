#!/bin/bash
set -e

echo "=== Dating App Deploy ==="
echo ""

# Pull latest code
echo "Pulling latest code..."
git pull

# Build images
echo "Building images..."
docker compose build

# Stop old containers
echo "Stopping old containers..."
docker compose down

# Start all services
echo "Starting services..."
docker compose up -d

# Wait for app to be healthy
echo "Waiting for app to start..."
sleep 5

# Check health
if curl -sf http://localhost/health > /dev/null 2>&1; then
    echo ""
    echo "✅ Deploy complete! App is running at http://localhost"
else
    echo ""
    echo "⚠️  App started but health check failed. Check logs:"
    echo "   docker compose logs app"
fi
