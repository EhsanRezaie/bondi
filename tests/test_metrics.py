# tests/done/test_metrics.py
import pytest
from httpx import AsyncClient


class TestMetricsEndpoint:

    async def test_metrics_returns_prometheus_text(self, client: AsyncClient):
        res = await client.get("/metrics")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/plain")
        body = res.text
        assert "http_requests_total" in body
        assert "http_request_duration_seconds" in body
        assert "bondi_db_pool_checked_out" in body
        assert "bondi_ws_active_connections" in body

    async def test_metrics_not_in_openapi(self):
        from app.main import app
        paths = app.openapi()["paths"]
        assert "/metrics" not in paths

    async def test_health_endpoints_still_work(self, client: AsyncClient):
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"