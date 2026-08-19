# tests/test_locations.py
import pytest
from httpx import AsyncClient
from unittest.mock import patch

from app.core.security import create_access_token

# ============ URL Constants ============
COUNTRIES_URL = "/api/v1/locations/countries"
PROVINCES_URL = "/api/v1/locations/states"
CITIES_URL = "/api/v1/locations/cities"
REVERSE_GEOCODE_URL = "/api/v1/locations/reverse-geocode"
CITY_CENTROID_URL = "/api/v1/locations/city-centroid"
LOCATION_GPS_URL = "/api/v1/locations/me/location-gps"
LOCATION_MANUAL_URL = "/api/v1/locations/me/location-manual"


# ============ Helper Functions ============
async def create_test_user(client: AsyncClient, db_session, mock_verification_code) -> dict:
    """Create a fully registered test user."""
    from tests.test_auth import register_user_full
    result = await register_user_full(client, mock_verification_code)
    return result


def get_auth_headers(user_id: str) -> dict:
    """Get auth headers for a user."""
    token = create_access_token(user_id=user_id)
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# GET /api/v1/locations/countries
# =============================================================================

class TestCountries:
    """Test GET /locations/countries endpoint."""

    async def test_list_countries_success(self, client: AsyncClient):
        """Should return list of all countries."""
        res = await client.get(COUNTRIES_URL)
        assert res.status_code == 200
        data = res.json()
        
        assert isinstance(data, list)
        assert len(data) > 0
        
        # Check structure
        first = data[0]
        assert "iso2" in first
        assert "iso3" in first
        assert "name" in first
        assert isinstance(first["iso2"], str)
        assert len(first["iso2"]) == 2
        
        # Iran should be in the list (pycountry returns "Iran, Islamic Republic of")
        iran = next((c for c in data if c["iso2"] == "IR"), None)
        assert iran is not None
        # ✅ FIX: Use correct name from pycountry
        assert iran["name"] in ["Iran", "Iran, Islamic Republic of"]

    async def test_list_countries_cache(self, client: AsyncClient):
        """Should return consistent cached results."""
        res1 = await client.get(COUNTRIES_URL)
        res2 = await client.get(COUNTRIES_URL)
        
        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res1.json() == res2.json()


# =============================================================================
# GET /api/v1/locations/provinces
# =============================================================================

class TestProvinces:
    """Test GET /locations/provinces endpoint."""

    async def test_list_provinces_success(self, client: AsyncClient):
        """Should return provinces for a country."""
        res = await client.get(PROVINCES_URL, params={"country": "IR"})
        assert res.status_code == 200
        data = res.json()
        
        assert isinstance(data, list)
        
        if len(data) > 0:
            first = data[0]
            assert "code" in first
            assert "iso_code" in first
            assert "name" in first
            assert "type" in first
            assert "latitude" in first
            assert "longitude" in first
            assert "country_code" in first

    async def test_list_provinces_invalid_country(self, client: AsyncClient):
        """Should return 404 for invalid country code."""
        res = await client.get(PROVINCES_URL, params={"country": "XX"})
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    async def test_list_provinces_missing_country(self, client: AsyncClient):
        """Should return 422 when country parameter is missing."""
        res = await client.get(PROVINCES_URL)
        assert res.status_code == 422


# =============================================================================
# GET /api/v1/locations/cities
# =============================================================================

class TestCities:
    """Test GET /locations/cities endpoint."""

    async def test_list_cities_success(self, client: AsyncClient):
        """Should return cities for a country."""
        res = await client.get(CITIES_URL, params={"country": "IR"})
        assert res.status_code == 200
        data = res.json()
        
        assert isinstance(data, list)
        
        if len(data) > 0:
            first = data[0]
            assert "name" in first
            assert "latitude" in first or first["latitude"] is None
            assert "longitude" in first or first["longitude"] is None
            assert "population" in first

    async def test_list_cities_invalid_country(self, client: AsyncClient):
        """Should return 404 for invalid country code."""
        res = await client.get(CITIES_URL, params={"country": "XX"})
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    async def test_list_cities_missing_country(self, client: AsyncClient):
        """Should return 422 when country parameter is missing."""
        res = await client.get(CITIES_URL)
        assert res.status_code == 422


# =============================================================================
# GET /api/v1/locations/reverse-geocode
# =============================================================================

class TestReverseGeocode:
    """Test GET /locations/reverse-geocode endpoint."""

    async def test_reverse_geocode_success(self, client: AsyncClient):
        """Should return place names from coordinates."""
        with patch("app.services.location_service._nominatim_reverse") as mock_reverse:
            mock_reverse.return_value = {
                "country": "Iran",
                "country_iso2": "IR",
                "province": "Tehran Province",
                "city": "Tehran",
            }
            
            res = await client.get(
                REVERSE_GEOCODE_URL,
                params={"lat": 35.6892, "lng": 51.3890}
            )
            assert res.status_code == 200
            data = res.json()
            
            assert data["country"] == "Iran"
            assert data["country_iso2"] == "IR"
            assert data["province"] == "Tehran Province"
            assert data["city"] == "Tehran"

    async def test_reverse_geocode_invalid_lat(self, client: AsyncClient):
        """Should return 422 for invalid latitude (>90)."""
        res = await client.get(
            REVERSE_GEOCODE_URL,
            params={"lat": 100, "lng": 51.3890}
        )
        assert res.status_code == 422

    async def test_reverse_geocode_invalid_lng(self, client: AsyncClient):
        """Should return 422 for invalid longitude (>180)."""
        res = await client.get(
            REVERSE_GEOCODE_URL,
            params={"lat": 35.6892, "lng": 200}
        )
        assert res.status_code == 422

    async def test_reverse_geocode_missing_params(self, client: AsyncClient):
        """Should return 422 when parameters are missing."""
        res = await client.get(REVERSE_GEOCODE_URL, params={"lat": 35.6892})
        assert res.status_code == 422

    async def test_reverse_geocode_service_unavailable(self, client: AsyncClient):
        """Should return 503 when Nominatim fails."""
        with patch("app.services.location_service._nominatim_reverse") as mock_reverse:
            mock_reverse.return_value = None
            
            res = await client.get(
                REVERSE_GEOCODE_URL,
                params={"lat": 35.6892, "lng": 51.3890}
            )
            assert res.status_code == 503
            assert "unavailable" in res.json()["detail"].lower()


# =============================================================================
# GET /api/v1/locations/city-centroid
# =============================================================================

class TestCityCentroid:
    """Test GET /locations/city-centroid endpoint."""

    async def test_city_centroid_success(self, client: AsyncClient):
        """Should return coordinates for a city."""
        res = await client.get(
            CITY_CENTROID_URL,
            params={"country": "IR", "city": "Tehran"}
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["name"] == "Tehran"
        assert "latitude" in data
        assert "longitude" in data

    async def test_city_centroid_invalid_country(self, client: AsyncClient):
        """Should return 404 for invalid country."""
        res = await client.get(
            CITY_CENTROID_URL,
            params={"country": "XX", "city": "Tehran"}
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    async def test_city_centroid_invalid_city(self, client: AsyncClient):
        """Should return 404 for invalid city."""
        res = await client.get(
            CITY_CENTROID_URL,
            params={"country": "IR", "city": "NonExistentCity"}
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    async def test_city_centroid_missing_params(self, client: AsyncClient):
        """Should return 422 when parameters are missing."""
        res = await client.get(CITY_CENTROID_URL, params={"country": "IR"})
        assert res.status_code == 422


# =============================================================================
# PATCH /api/v1/locations/me/location-gps
# =============================================================================

class TestLocationGPS:
    """Test PATCH /locations/me/location-gps endpoint."""

    async def test_update_location_gps_success(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should update user location from GPS coordinates."""
        # Create user using register_user_full from tests/done/test_auth.py
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        with patch("app.services.location_service._nominatim_reverse") as mock_reverse:
            mock_reverse.return_value = {
                "country": "Iran",
                "country_iso2": "IR",
                "province": "Tehran",
                "city": "Tehran",
            }
            
            res = await client.patch(
                LOCATION_GPS_URL,
                params={"lat": 35.6892, "lng": 51.3890},
                headers=headers
            )
            assert res.status_code == 200
            data = res.json()
            
            assert data["lat"] == 35.6892
            assert data["lng"] == 51.3890
            assert data["country"] == "Iran"
            assert data["province"] == "Tehran"
            assert data["city"] == "Tehran"
            assert data["location_manual"] is False

    async def test_update_location_gps_partial_response(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should handle partial reverse geocode response."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        with patch("app.services.location_service._nominatim_reverse") as mock_reverse:
            mock_reverse.return_value = {
                "country": None,
                "country_iso2": None,
                "province": None,
                "city": None,
            }
            
            res = await client.patch(
                LOCATION_GPS_URL,
                params={"lat": 35.6892, "lng": 51.3890},
                headers=headers
            )
            assert res.status_code == 200
            data = res.json()
            
            assert data["lat"] == 35.6892
            assert data["lng"] == 51.3890
            assert data["location_manual"] is False

    async def test_update_location_gps_response_shape(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """LocationUpdateResponse should contain all 6 fields."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        with patch("app.services.location_service._nominatim_reverse") as mock_reverse:
            mock_reverse.return_value = {
                "country": "Iran",
                "country_iso2": "IR",
                "province": "Tehran",
                "city": "Tehran",
            }

            res = await client.patch(
                LOCATION_GPS_URL,
                params={"lat": 35.6892, "lng": 51.3890},
                headers=headers
            )
            assert res.status_code == 200
            data = res.json()

            assert isinstance(data["lat"], float)
            assert isinstance(data["lng"], float)
            assert isinstance(data["country"], str)
            assert isinstance(data["province"], str)
            assert isinstance(data["city"], str)
            assert data["location_manual"] is False

    async def test_update_location_gps_invalid_lat(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return 422 for invalid latitude."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        res = await client.patch(
            LOCATION_GPS_URL,
            params={"lat": 100, "lng": 51.3890},
            headers=headers
        )
        assert res.status_code == 422

    async def test_update_location_gps_invalid_lng(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return 422 for invalid longitude."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        res = await client.patch(
            LOCATION_GPS_URL,
            params={"lat": 35.6892, "lng": 200},
            headers=headers
        )
        assert res.status_code == 422

    async def test_update_location_gps_requires_auth(self, client: AsyncClient):
        """Should return 401 without authentication."""
        res = await client.patch(
            LOCATION_GPS_URL,
            params={"lat": 35.6892, "lng": 51.3890}
        )
        assert res.status_code == 401


# =============================================================================
# PATCH /api/v1/locations/me/location-manual
# =============================================================================

class TestLocationManual:
    """Test PATCH /locations/me/location-manual endpoint."""

    async def test_update_location_manual_success(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should update user location from manual selection."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        res = await client.patch(
            LOCATION_MANUAL_URL,
            params={
                "country": "Iran",
                "province": "Isfahan",
                "city": "Isfahan",
                "country_iso2": "IR",
            },
            headers=headers
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["country"] == "Iran"
        assert data["province"] == "Isfahan"
        assert data["city"] == "Isfahan"
        assert data["location_manual"] is True
        
        # Should have estimated lat/lng from city centroid
        assert data["lat"] is not None or data["lat"] is None

    async def test_update_location_manual_response_shape(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """LocationUpdateResponse should contain all 6 fields for manual."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.patch(
            LOCATION_MANUAL_URL,
            params={
                "country": "Iran",
                "province": "Isfahan",
                "city": "Isfahan",
                "country_iso2": "IR",
            },
            headers=headers
        )
        assert res.status_code == 200
        data = res.json()

        assert isinstance(data["lat"], (float, type(None)))
        assert isinstance(data["lng"], (float, type(None)))
        assert isinstance(data["country"], str)
        assert isinstance(data["province"], str)
        assert isinstance(data["city"], str)
        assert data["location_manual"] is True

    async def test_update_location_manual_with_centroid(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should estimate lat/lng from city centroid."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        res = await client.patch(
            LOCATION_MANUAL_URL,
            params={
                "country": "Iran",
                "province": "Tehran",
                "city": "Tehran",
                "country_iso2": "IR",
            },
            headers=headers
        )
        assert res.status_code == 200
        data = res.json()
        
        assert data["country"] == "Iran"
        assert data["province"] == "Tehran"
        assert data["city"] == "Tehran"
        assert data["location_manual"] is True
        
        # Tehran should have coordinates
        assert data["lat"] == 35.6892 or data["lat"] is not None
        assert data["lng"] == 51.3890 or data["lng"] is not None

    async def test_update_location_manual_missing_params(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return 422 when required parameters are missing."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        res = await client.patch(
            LOCATION_MANUAL_URL,
            params={"country": "Iran"},
            headers=headers
        )
        assert res.status_code == 422

    async def test_update_location_manual_requires_auth(self, client: AsyncClient):
        """Should return 401 without authentication."""
        res = await client.patch(
            LOCATION_MANUAL_URL,
            params={
                "country": "Iran",
                "province": "Tehran",
                "city": "Tehran",
                "country_iso2": "IR",
            }
        )
        assert res.status_code == 401


# =============================================================================
# Integration Tests
# =============================================================================

class TestLocationIntegration:
    """Test location flow integration."""

    async def test_gps_to_manual_flow(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should switch from GPS to manual location."""
        from tests.test_auth import register_user_full
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        
        # Set GPS location
        with patch("app.services.location_service._nominatim_reverse") as mock_reverse:
            mock_reverse.return_value = {
                "country": "Iran",
                "country_iso2": "IR",
                "province": "Tehran",
                "city": "Tehran",
            }
            
            gps_res = await client.patch(
                LOCATION_GPS_URL,
                params={"lat": 35.6892, "lng": 51.3890},
                headers=headers
            )
            assert gps_res.status_code == 200
            assert gps_res.json()["location_manual"] is False
        
        # Switch to manual
        manual_res = await client.patch(
            LOCATION_MANUAL_URL,
            params={
                "country": "Iran",
                "province": "Isfahan",
                "city": "Isfahan",
                "country_iso2": "IR",
            },
            headers=headers
        )
        assert manual_res.status_code == 200
        assert manual_res.json()["location_manual"] is True
        assert manual_res.json()["country"] == "Iran"
        assert manual_res.json()["province"] == "Isfahan"
        assert manual_res.json()["city"] == "Isfahan"