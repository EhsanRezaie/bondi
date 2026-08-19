# tests/test_system.py
import pytest
import json
from pathlib import Path
from httpx import AsyncClient
from unittest.mock import patch

from app.core.config import settings
from app.api.v1.endpoints.system import MAINTENANCE_FILE, VERSION_OVERRIDE_FILE

SYSTEM_STATUS_URL = "/api/v1/system/status"
VERSION_CHECK_URL = "/api/v1/system/version-check"
MAINTENANCE_ENABLE_URL = "/api/v1/system/maintenance/enable"
MAINTENANCE_DISABLE_URL = "/api/v1/system/maintenance/disable"
MAINTENANCE_STATUS_URL = "/api/v1/system/maintenance/status"
VERSION_SET_MINIMUM_URL = "/api/v1/system/version/set-minimum"
VERSION_FORCE_UPDATE_URL = "/api/v1/system/version/force-update"
VERSION_CONFIG_URL = "/api/v1/system/version/config"
VERSION_OVERRIDE_DELETE_URL = "/api/v1/system/version/override"


@pytest.fixture(autouse=True)
def cleanup_files():
    """Clean up maintenance and version override files before and after tests."""
    # Before test
    if MAINTENANCE_FILE.exists():
        MAINTENANCE_FILE.unlink()
    if VERSION_OVERRIDE_FILE.exists():
        VERSION_OVERRIDE_FILE.unlink()
    
    yield
    
    # After test
    if MAINTENANCE_FILE.exists():
        MAINTENANCE_FILE.unlink()
    if VERSION_OVERRIDE_FILE.exists():
        VERSION_OVERRIDE_FILE.unlink()


@pytest.fixture
def admin_headers() -> dict:
    """Create admin auth headers."""
    return {"X-Admin-Key": settings.ADMIN_SECRET_KEY}


class TestSystemStatus:
    """Test /system/status endpoint."""
    
    async def test_system_status_ok(
        self,
        client: AsyncClient,
    ):
        """Should return system status with all services ok."""
        res = await client.get(SYSTEM_STATUS_URL)
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] in ["ok", "degraded"]
        assert "timestamp" in data
        assert "version" in data
        assert "environment" in data
        assert "services" in data
        assert "maintenance" in data
        
        services = data["services"]
        assert "database" in services
        assert "redis" in services
        assert "storage" in services
        
        maintenance = data["maintenance"]
        assert "enabled" in maintenance
        assert maintenance["enabled"] is False
    
    async def test_system_status_maintenance_mode(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should show maintenance mode when enabled."""
        # Enable maintenance
        res = await client.post(
            MAINTENANCE_ENABLE_URL,
            json={"message": "System maintenance"},
            headers=admin_headers,
        )
        assert res.status_code == 200
        
        res = await client.get(SYSTEM_STATUS_URL)
        assert res.status_code == 200
        data = res.json()
        
        assert data["maintenance"]["enabled"] is True
        assert data["maintenance"]["message"] == "System maintenance"
        assert data["maintenance"]["start_time"] is not None
        
        # Cleanup
        await client.post(MAINTENANCE_DISABLE_URL, headers=admin_headers)


class TestVersionCheck:
    """Test /system/version-check endpoint."""
    
    async def test_version_check_ok(
        self,
        client: AsyncClient,
    ):
        """Should return ok when version is compatible."""
        res = await client.post(
            VERSION_CHECK_URL,
            json={
                "platform": "android",
                "version": "1.0.0",
            },
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert data["current_version"] == "1.0.0"
        assert data["platform"] == "android"
        assert data["force_update"] is False
        assert data["minimum_version"] is not None
    
    async def test_version_check_ios(
        self,
        client: AsyncClient,
    ):
        """Should work for iOS platform."""
        res = await client.post(
            VERSION_CHECK_URL,
            json={
                "platform": "ios",
                "version": "1.0.0",
            },
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert data["platform"] == "ios"
    
    async def test_version_check_update_required(
        self,
        client: AsyncClient,
    ):
        """Should return update_required when version is too old."""
        with patch("app.core.config.settings.MIN_ANDROID_VERSION", "2.0.0"):
            res = await client.post(
                VERSION_CHECK_URL,
                json={
                    "platform": "android",
                    "version": "1.5.0",
                },
            )
            assert res.status_code == 200
            data = res.json()
            
            assert data["status"] == "update_required"
            assert data["current_version"] == "1.5.0"
            assert data["minimum_version"] == "2.0.0"
            assert data["update_url"] is not None
    
    async def test_version_check_force_update(
        self,
        client: AsyncClient,
    ):
        """Should return force_update=True when enabled."""
        with patch("app.core.config.settings.FORCE_UPDATE_ENABLED", True):
            with patch("app.core.config.settings.MIN_ANDROID_VERSION", "2.0.0"):
                res = await client.post(
                    VERSION_CHECK_URL,
                    json={
                        "platform": "android",
                        "version": "1.5.0",
                    },
                )
                assert res.status_code == 200
                data = res.json()
                
                assert data["status"] == "update_required"
                assert data["force_update"] is True
                assert data["message"] == settings.FORCE_UPDATE_MESSAGE
    
    async def test_version_check_maintenance_mode(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should return maintenance status when maintenance is enabled."""
        # Enable maintenance
        await client.post(
            MAINTENANCE_ENABLE_URL,
            json={"message": "Maintenance"},
            headers=admin_headers,
        )
        
        res = await client.post(
            VERSION_CHECK_URL,
            json={
                "platform": "android",
                "version": "1.0.0",
            },
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "maintenance"
        assert data["message"] == "Maintenance"
        
        # Cleanup
        await client.post(MAINTENANCE_DISABLE_URL, headers=admin_headers)
    
    async def test_version_check_invalid_platform(
        self,
        client: AsyncClient,
    ):
        """Should handle invalid platform gracefully."""
        res = await client.post(
            VERSION_CHECK_URL,
            json={
                "platform": "windows",
                "version": "1.0.0",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["platform"] == "windows"
        assert data["status"] in ["ok", "update_required"]
    
    async def test_version_check_missing_fields(
        self,
        client: AsyncClient,
    ):
        """Should return 422 when required fields are missing."""
        res = await client.post(
            VERSION_CHECK_URL,
            json={"platform": "android"},
        )
        assert res.status_code == 422


class TestMaintenanceAdmin:
    """Test maintenance admin endpoints."""
    
    async def test_enable_maintenance_success(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should enable maintenance mode."""
        res = await client.post(
            MAINTENANCE_ENABLE_URL,
            json={"message": "Scheduled maintenance"},
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert data["message"] == "Maintenance mode enabled"
        assert data["data"]["enabled"] is True
        assert data["data"]["message"] == "Scheduled maintenance"
        
        # Verify file exists
        assert MAINTENANCE_FILE.exists()
        
        # Cleanup
        await client.post(MAINTENANCE_DISABLE_URL, headers=admin_headers)
    
    async def test_enable_maintenance_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.post(
            MAINTENANCE_ENABLE_URL,
            json={"message": "Maintenance"},
        )
        assert res.status_code == 403
    
    async def test_disable_maintenance_success(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should disable maintenance mode."""
        # Enable first
        await client.post(
            MAINTENANCE_ENABLE_URL,
            json={"message": "Maintenance"},
            headers=admin_headers,
        )
        assert MAINTENANCE_FILE.exists()
        
        # Disable
        res = await client.post(
            MAINTENANCE_DISABLE_URL,
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert data["message"] == "Maintenance mode disabled"
        assert data["data"]["enabled"] is False
        
        # Verify file deleted
        assert not MAINTENANCE_FILE.exists()
    
    async def test_disable_maintenance_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.post(MAINTENANCE_DISABLE_URL)
        assert res.status_code == 403
    
    async def test_maintenance_status_success(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should get maintenance status."""
        res = await client.get(
            MAINTENANCE_STATUS_URL,
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert "maintenance_mode" in data
        assert data["maintenance_mode"] is False
    
    async def test_maintenance_status_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.get(MAINTENANCE_STATUS_URL)
        assert res.status_code == 403


class TestVersionAdmin:
    """Test version control admin endpoints."""
    
    async def test_set_minimum_version_success(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should set minimum version for a platform."""
        res = await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "android", "version": "2.0.0"},
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert "Minimum version for android set to 2.0.0" in data["message"]
        assert data["data"]["minimum_versions"]["android"] == "2.0.0"
        
        # Verify override file exists
        assert VERSION_OVERRIDE_FILE.exists()
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)
    
    async def test_set_minimum_version_ios(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should set minimum version for iOS."""
        res = await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "ios", "version": "2.0.0"},
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["data"]["minimum_versions"]["ios"] == "2.0.0"
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)
    
    async def test_set_minimum_version_invalid_platform(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should return 400 for invalid platform."""
        res = await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "windows", "version": "2.0.0"},
            headers=admin_headers,
        )
        assert res.status_code == 400
        assert "Platform must be" in res.json()["detail"]
    
    async def test_set_minimum_version_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "android", "version": "2.0.0"},
        )
        assert res.status_code == 403
    
    async def test_force_update_enable(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should enable force update."""
        res = await client.post(
            VERSION_FORCE_UPDATE_URL,
            params={"force": True, "message": "Critical update"},
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert data["data"]["force_update"] is True
        assert data["data"]["force_update_message"] == "Critical update"
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)
    
    async def test_force_update_disable(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should disable force update."""
        # Enable first
        await client.post(
            VERSION_FORCE_UPDATE_URL,
            params={"force": True},
            headers=admin_headers,
        )
        
        # Disable
        res = await client.post(
            VERSION_FORCE_UPDATE_URL,
            params={"force": False},
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["data"]["force_update"] is False
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)
    
    async def test_force_update_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.post(
            VERSION_FORCE_UPDATE_URL,
            params={"force": True},
        )
        assert res.status_code == 403
    
    async def test_get_version_config(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should get version configuration."""
        # Set some overrides
        await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "android", "version": "2.0.0"},
            headers=admin_headers,
        )
        await client.post(
            VERSION_FORCE_UPDATE_URL,
            params={"force": True, "message": "Test message"},
            headers=admin_headers,
        )
        
        res = await client.get(
            VERSION_CONFIG_URL,
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert "data" in data
        config = data["data"]
        
        assert "minimum_versions" in config
        assert config["minimum_versions"]["android"] == "2.0.0"
        assert config["force_update"] is True
        assert config["force_update_message"] == "Test message"
        assert "app_version" in config
        assert "play_store_url" in config
        assert "app_store_url" in config
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)
    
    async def test_get_version_config_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.get(VERSION_CONFIG_URL)
        assert res.status_code == 403
    
    async def test_clear_version_override(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should clear version overrides."""
        # Set override
        await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "android", "version": "2.0.0"},
            headers=admin_headers,
        )
        assert VERSION_OVERRIDE_FILE.exists()
        
        # Clear
        res = await client.delete(
            VERSION_OVERRIDE_DELETE_URL,
            headers=admin_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        assert "overrides cleared" in data["message"].lower()
        assert not VERSION_OVERRIDE_FILE.exists()
    
    async def test_clear_version_override_unauthorized(
        self,
        client: AsyncClient,
    ):
        """Should return 403 without admin auth."""
        res = await client.delete(VERSION_OVERRIDE_DELETE_URL)
        assert res.status_code == 403


class TestVersionEdgeCases:
    """Test version edge cases."""
    
    async def test_version_compare_equal(self):
        """Should handle equal versions."""
        from app.api.v1.endpoints.system import version_compare
        
        assert version_compare("1.0.0", "1.0.0") == 0
        assert version_compare("2.1.3", "2.1.3") == 0
    
    async def test_version_compare_greater(self):
        """Should handle greater versions."""
        from app.api.v1.endpoints.system import version_compare
        
        assert version_compare("2.0.0", "1.0.0") == 1
        assert version_compare("1.1.0", "1.0.9") == 1
        assert version_compare("2.0.0", "1.9.9") == 1
    
    async def test_version_compare_less(self):
        """Should handle lesser versions."""
        from app.api.v1.endpoints.system import version_compare
        
        assert version_compare("1.0.0", "2.0.0") == -1
        assert version_compare("1.0.9", "1.1.0") == -1
        assert version_compare("1.9.9", "2.0.0") == -1
    
    async def test_version_compare_different_lengths(self):
        """Should handle versions with different number of parts."""
        from app.api.v1.endpoints.system import version_compare
        
        assert version_compare("1.0", "1.0.0") == 0
        assert version_compare("1.1", "1.0.9") == 1
        assert version_compare("1.0.0", "1.0") == 0
    
    async def test_version_check_old_version_with_override(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should use override minimum version in version check."""
        # Set override
        await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "android", "version": "2.0.0"},
            headers=admin_headers,
        )
        
        # ✅ Verify the override file exists and has the correct content
        assert VERSION_OVERRIDE_FILE.exists()
        with open(VERSION_OVERRIDE_FILE, "r") as f:
            content = json.load(f)
            print(f"Override content: {content}")
            assert content["minimum_versions"]["android"] == "2.0.0"
        
        # Now check version
        res = await client.post(
            VERSION_CHECK_URL,
            json={
                "platform": "android",
                "version": "1.5.0",
            },
        )
        assert res.status_code == 200
        data = res.json()
        print(f"Version check response: {data}")
        
        assert data["status"] == "update_required"
        assert data["minimum_version"] == "2.0.0"
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)
    
    async def test_version_check_new_version_with_override(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ):
        """Should pass version check with new version."""
        # Set override
        await client.post(
            VERSION_SET_MINIMUM_URL,
            params={"platform": "android", "version": "2.0.0"},
            headers=admin_headers,
        )
        
        res = await client.post(
            VERSION_CHECK_URL,
            json={
                "platform": "android",
                "version": "2.5.0",
            },
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["status"] == "ok"
        
        # Cleanup
        await client.delete(VERSION_OVERRIDE_DELETE_URL, headers=admin_headers)